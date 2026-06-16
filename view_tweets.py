import os
import sys
import requests
import json   
import time
from pathlib import Path
import logging
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# .env ファイルを明示的に読み込む
env_file = Path(__file__).parent / ".env"
load_dotenv(env_file)


# 標準出力/標準エラーのエンコーディングを UTF-8 に設定し、
# 表示できない文字は置換する（Windows の cp932 環境での UnicodeEncodeError 対策）
try:
    # Python 3.7+ の場合
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    try:
        import io

        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        # どの方法でも設定できなければ無視（最低限例外は出ないように）
        pass

# 環境変数から Bearer Token を取得
BEARER = os.getenv("X_BEARER_TOKEN")

# ツイート検索用URL（公式API v2）
SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

# スタブファイルのパス
STUB_FILE = Path(__file__).parent / "stub_tweet.json"

# ヘッダに Bearer Token を設定
headers = {
    "Authorization": f"Bearer {BEARER}"
}


def get_db_connection():
    """DB接続"""
    dsn = os.getenv('SUPABASE_DB_URL')
    if not dsn:
        raise RuntimeError('SUPABASE_DB_URL is not set')
    return psycopg2.connect(dsn)


def save_tweets_to_db(tweets_data, monitor, logger=None):
    """
    ツイートデータをDBに登録する。
    tweets_data: APIから返されたJSONデータ
    monitor: モニター設定辞書 {'monitor_id': ..., 'user_id': ..., ...}
    logger: ロギングオブジェクト
    戻り値: (成功件数, 失敗件数)
    """
    success_count = 0
    fail_count = 0
    
    if not tweets_data or 'data' not in tweets_data:
        msg = "有効なツイートデータがありません"
        if logger:
            logger.warning(msg)
        return success_count, fail_count
    
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # 現在の最大 result_id を取得して次の値を計算
        cur.execute("SELECT COALESCE(MAX(result_id), 0) + 1 as next_id FROM monitor_results")
        next_result_id = cur.fetchone()['next_id']
        
        for tweet in tweets_data.get('data', []):
            try:
                tweet_id = tweet.get('id')
                text = tweet.get('text')
                author_id = tweet.get('author_id')
                
                # user_handle は includes.users から取得
                user_handle = None
                includes = tweets_data.get('includes', {})
                for user in includes.get('users', []):
                    if user.get('id') == author_id:
                        user_handle = user.get('username')
                        break
                
                # ツイート情報を monitor_results テーブルに登録
                post_url = f"https://twitter.com/{user_handle}/status/{tweet_id}" if user_handle else None
                hashtags = tweet.get('hashtags')
                cur.execute(
                    """
                    INSERT INTO monitor_results (result_id, monitor_id, user_id, post_id, user_handle, content, hashtags, post_url, posted_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (result_id, monitor_id, user_id) DO NOTHING
                    """,
                    (next_result_id, monitor['monitor_id'], monitor['user_id'], tweet_id, user_handle, text, json.dumps(hashtags, ensure_ascii=False) if hashtags is not None else None, post_url)
                )
                
                if cur.rowcount > 0:
                    success_count += 1
                    if logger:
                        logger.info(f"ツイート保存成功: {tweet_id}")
                else:
                    fail_count += 1
                    if logger:
                        logger.warning(f"ツイート重複スキップ: {tweet_id}")
                        
                next_result_id += 1  # 次のツイート用にインクリメント
                    
            except Exception as e:
                fail_count += 1
                import traceback
                error_detail = traceback.format_exc()
                msg = f"ツイート保存失敗 ({tweet.get('id')}): {str(e)}\n{error_detail}"
                if logger:
                    logger.error(msg)
        
        conn.commit()
        cur.close()
        conn.close()
        
        if logger:
            logger.info(f"DB登録完了 - 成功: {success_count}, 失敗: {fail_count}")
        
    except Exception as e:
        msg = f"DB接続エラー: {str(e)}"
        if logger:
            logger.error(msg)
    
    return success_count, fail_count


def search_tweets(logger=None):
    """
    有効なモニター設定からキーワードを取得し、ツイートを検索する。
    use_mock: Trueならスタブデータを使用
    logger: ロギングオブジェクト
    戻り値: (results_list, errors_list)
    results_list: [{'monitor_id': 1, 'keywords': [...], 'data': {...}}, ...]
    errors_list: エラーメッセージのリスト
    """
    results = []
    errors = []
    
    # DBから有効なモニター設定を取得
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT monitor_id, user_id, monitor_name, keywords, notify_email,
                   is_enabled, created_at, updated_at
            FROM monitor_settings
            WHERE is_enabled = TRUE
            ORDER BY monitor_id
            """
        )
        monitors = cur.fetchall()
        cur.close()
        conn.close()
        
        if not monitors:
            msg = "[WARNING] 有効なモニター設定がありません。"
            if logger:
                logger.warning(msg)
            errors.append(msg)
            return results, errors
        
    except Exception as e:
        msg = f"[ERROR] DBからモニター設定取得失敗: {str(e)}"
        if logger:
            logger.error(msg)
        errors.append(msg)
        return results, errors
    
    # 各モニターごとにツイートを検索
    for monitor in monitors:
        monitor_id = monitor['monitor_id']
        keywords = monitor['keywords'] if isinstance(monitor['keywords'], list) else json.loads(monitor['keywords'])
        query = " OR ".join(keywords)
        
        if logger:
            logger.info(f"モニターID {monitor_id}: キーワード={keywords}")
        
        # 検索条件
        params = {
            "query": query,
            "max_results": 10,
            "tweet.fields": "public_metrics",
            "user.fields": "id,name,username,profile_image_url,verified,created_at"
        }
        
        # モックモードの判定：スタブファイルが存在すればモックを使用
        if STUB_FILE.exists():
            try:
                with open(STUB_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if logger:
                    logger.info(f"[MOCK] モニターID {monitor_id}")
                results.append({'monitor': monitor, 'keywords': keywords, 'data': data})
                continue
            except FileNotFoundError:
                # ファイルが見つからない場合は次のモニターへ（STUB_FILE.exists() と矛盾する場合はここに到達）
                msg = f"[WARNING] {STUB_FILE} not found"
                if logger:
                    logger.warning(msg)
                errors.append(msg)
                continue
        
        # APIを呼び出す
        if logger:
            logger.info(f"API呼び出し中... (モニターID {monitor_id})")
        response = requests.get(SEARCH_URL, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            if logger:
                logger.info(f"API呼び出し成功 (モニターID {monitor_id})")
            results.append({'monitor': monitor, 'keywords': keywords, 'data': data})
        elif response.status_code == 429:
            reset_time = int(response.headers.get("x-rate-limit-reset", 0))
            current_time = int(time.time())
            wait_seconds = max(0, reset_time - current_time)
            error_msg = f"[RATE_LIMIT] モニターID {monitor_id}: {wait_seconds}秒待機が必要"
            if logger:
                logger.warning(error_msg)
            errors.append(error_msg)
        else:
            error_msg = f"[API_ERROR] モニターID {monitor_id}: {response.text}"
            if logger:
                logger.error(error_msg)
            errors.append(error_msg)
    
    return results, errors


# CLI/entrypoint has been moved to `main.py`.
# Keep this module focused on functions: get_db_connection, search_tweets, save_tweets_to_db
