import os
import sys
import requests
import json
import re
import time
from pathlib import Path
from datetime import datetime
import logging
import psycopg2
import psycopg2.extras
from dotenv import dotenv_values, load_dotenv

# .env ファイルを明示的に読み込む
env_file = Path(__file__).parent / ".env_bat"
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

# .env_bat から Bearer Token を直接取得
BEARER = dotenv_values(env_file).get("X_BEARER_TOKEN")

# ツイート検索用URL（公式API v2）
SEARCH_URL = "https://api.x.com/2/tweets/search/recent"

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


def normalize_hashtags(raw_hashtags):
    """
    X API の entities.hashtags は次のような辞書配列を返す:
    [{'tag': 'ぽこあポケモン', 'start': 34, 'end': 42}]
    これを ['ぽこあポケモン'] のようなタグ名一覧に正規化する。
    """
    if raw_hashtags is None:
        return []

    normalized = []
    seen = set()

    def add_tag(tag_value):
        if tag_value is None:
            return
        tag_text = str(tag_value).strip()
        if not tag_text:
            return
        tag_text = tag_text.lstrip('#').strip()
        if not tag_text or tag_text.lower() in {'tag', 'start', 'end'}:
            return
        # 文字列化された dict から余計な metadata を取り除く
        tag_text = tag_text.replace("'", "").replace('"', '')
        tag_text = re.sub(r"[{}\[\]:,]", "", tag_text).strip()
        if not tag_text:
            return
        if tag_text not in seen:
            seen.add(tag_text)
            normalized.append(tag_text)

    if isinstance(raw_hashtags, dict):
        add_tag(raw_hashtags.get('tag'))
        return normalized

    if isinstance(raw_hashtags, str):
        value = raw_hashtags.strip()
        if not value:
            return []

        # 既に JSON 文字列の場合は再解釈して処理
        if value.startswith('{') or value.startswith('['):
            try:
                parsed = json.loads(value)
                return normalize_hashtags(parsed)
            except (TypeError, ValueError):
                pass

        # 例: "#{'end': 42, 'tag': 'ぽこあポケモン', 'start': 34}"
        for match in re.findall(r"'tag'\s*:\s*'([^']+)'", value):
            add_tag(match)
        if normalized:
            return normalized

        for token in re.split(r"[\s,]+", value):
            if not token:
                continue
            add_tag(token)
        return normalized

    if isinstance(raw_hashtags, (list, tuple)):
        for item in raw_hashtags:
            if isinstance(item, dict):
                add_tag(item.get('tag'))
            elif isinstance(item, str):
                for candidate in re.findall(r"'tag'\s*:\s*'([^']+)'", item):
                    add_tag(candidate)
                if not any(candidate for candidate in [item]):
                    add_tag(item)
            else:
                add_tag(item)
        return normalized

    add_tag(raw_hashtags)
    return normalized


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
                created_at = tweet.get('created_at')
                created_at_dt = None
                if created_at:
                    try:
                        created_at_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    except ValueError:
                        created_at_dt = None
                
                # user_handle は includes.users から取得
                user_handle = None
                includes = tweets_data.get('includes', {})
                for user in includes.get('users', []):
                    if user.get('id') == author_id:
                        user_handle = user.get('username')
                        break
                
                # ツイート情報を monitor_results テーブルに登録
                post_url = f"https://twitter.com/{user_handle}/status/{tweet_id}" if user_handle else None
                hashtags = normalize_hashtags(tweet.get("entities", {}).get("hashtags", []))

                # 同じ post_id のツイートは重複登録しない
                cur.execute(
                    "SELECT 1 FROM monitor_results WHERE post_id = %s LIMIT 1",
                    (tweet_id,)
                )
                if cur.fetchone():
                    fail_count += 1
                    if logger:
                        logger.warning(f"ツイート重複スキップ: {tweet_id} (post_id already exists)")
                    continue

                cur.execute(
                    """
                    INSERT INTO monitor_results (result_id, monitor_id, user_id, post_id, user_handle, content, hashtags, post_url, posted_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (result_id, monitor_id, user_id) DO NOTHING
                    """,
                    (next_result_id, monitor['monitor_id'], monitor['user_id'], tweet_id, user_handle, text, json.dumps(hashtags, ensure_ascii=False) if hashtags else None, post_url, created_at_dt)
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

    # すべての expansions を展開
    "expansions": (
        "author_id,"
        "referenced_tweets.id,"
        "referenced_tweets.id.author_id,"
        "attachments.media_keys,"
        "entities.mentions.username,"
        "in_reply_to_user_id,"
        "geo.place_id"
    ),

    # ツイートで取得できる全フィールド
    "tweet.fields": (
        "attachments,"
        "author_id,"
        "context_annotations,"
        "conversation_id,"
        "created_at,"
        "edit_controls,"
        "entities,"
        "geo,"
        "id,"
        "in_reply_to_user_id,"
        "lang,"
        "public_metrics,"
        "possibly_sensitive,"
        "referenced_tweets,"
        "reply_settings,"
        "source,"
        "text,"
        "withheld"
    ),

    # ユーザーで取得できる全フィールド
    "user.fields": (
        "created_at,"
        "description,"
        "entities,"
        "id,"
        "location,"
        "name,"
        "profile_image_url,"
        "protected,"
        "public_metrics,"
        "url,"
        "username,"
        "verified,"
        "withheld"
    ),

    # メディアで取得できる全フィールド（画像URL含む）
    "media.fields": (
        "duration_ms,"
        "height,"
        "media_key,"
        "preview_image_url,"
        "type,"
        "url,"
        "variants,"
        "width"
    )

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
                tweet_count = len(data.get('data', []))
                logger.info(
                    f"API取得データ (モニターID {monitor_id}, 件数={tweet_count}):\n"
                    f"{json.dumps(data, ensure_ascii=False, indent=2)}"
                )
                media_by_key = {
                    media.get('media_key'): media
                    for media in (data.get('includes') or {}).get('media', [])
                    if media.get('media_key')
                }
                for tweet in data.get('data', []) or []:
                    tweet_id = tweet.get('id')
                    media_keys = (tweet.get('attachments') or {}).get('media_keys', [])
                    for media_key in media_keys:
                        media = media_by_key.get(media_key)
                        if not media:
                            logger.warning(
                                f"メディア情報なし (モニターID {monitor_id}, "
                                f"投稿ID {tweet_id}, media_key {media_key})"
                            )
                            continue

                        display_url = media.get('url') or media.get('preview_image_url')
                        if display_url:
                            logger.info(
                                f"画像表示用URL (モニターID {monitor_id}, 投稿ID {tweet_id}, "
                                f"media_key {media_key}, type {media.get('type')}): {display_url}"
                            )
                        else:
                            logger.warning(
                                f"画像表示用URLなし (モニターID {monitor_id}, "
                                f"投稿ID {tweet_id}, media_key {media_key})"
                            )
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
