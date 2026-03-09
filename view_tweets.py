import os
import sys
import requests
import json   
import time
from pathlib import Path
import logging
import psycopg2
import psycopg2.extras


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
STUB_FILE = Path("stub_tweet.json")

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


def save_tweets_to_db(tweets_data, logger=None):
    """
    ツイートデータをDBに登録する。
    tweets_data: APIから返されたJSONデータ
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
        
        for tweet in tweets_data.get('data', []):
            try:
                tweet_id = tweet.get('id')
                text = tweet.get('text')
                author_id = tweet.get('author_id')
                
                # public_metrics を JSON 形式で保存
                public_metrics = tweet.get('public_metrics', {})
                retweet_count = public_metrics.get('retweet_count', 0)
                reply_count = public_metrics.get('reply_count', 0)
                like_count = public_metrics.get('like_count', 0)
                quote_count = public_metrics.get('quote_count', 0)
                
                # ツイートを tweets テーブルに登録（既に存在する場合は更新）
                cur.execute(
                    """
                    INSERT INTO tweets (tweet_id, text, author_id, retweet_count, reply_count, like_count, quote_count, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (tweet_id) DO UPDATE
                    SET retweet_count = EXCLUDED.retweet_count,
                        reply_count = EXCLUDED.reply_count,
                        like_count = EXCLUDED.like_count,
                        quote_count = EXCLUDED.quote_count,
                        updated_at = NOW()
                    """,
                    (tweet_id, text, author_id, retweet_count, reply_count, like_count, quote_count)
                )
                success_count += 1
                if logger:
                    logger.info(f"ツイート保存成功: {tweet_id}")
                    
            except Exception as e:
                fail_count += 1
                msg = f"ツイート保存失敗 ({tweet.get('id')}): {str(e)}"
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


def search_tweets(query, use_mock=False, logger=None):
    """
    ツイートを検索する関数。
    query: 検索ワード
    use_mock: Trueならスタブデータを使用
    logger: ロギングオブジェクト
    戻り値: (data, error_message, rate_limit_seconds)
    """
    if not query:
        msg = "[ERROR] 検索ワードが指定されていません。"
        if logger:
            logger.error(msg)
        return None, msg, None

    # 検索条件
    params = {
        "query": query,
        "max_results": 10,
        "tweet.fields": "public_metrics",
        "user.fields": "id,name,username,profile_image_url,verified,created_at"
    }

    # モックモードの判定
    if use_mock or STUB_FILE.exists():
        # スタブデータを使う
        try:
            with open(STUB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            msg = "[INFO] MOCK MODE ENABLED (API is not called)"
            if logger:
                logger.info(msg)
            print(msg)
            return data, None, None
        except FileNotFoundError:
            if use_mock:
                msg = "[WARNING] stub_tweets.json not found, falling back to API call"
                if logger:
                    logger.warning(msg)
                return None, msg, None
            use_mock = False

    if not use_mock:
        # APIを呼び出す
        if logger:
            logger.info("API呼び出し中...")
        response = requests.get(SEARCH_URL, headers=headers, params=params)

        if response.status_code == 200:
            data = response.json()
            if logger:
                logger.info("API呼び出し成功")
            return data, None, None
        elif response.status_code == 429:
            # レート制限に引っかかった場合
            reset_time = int(response.headers.get("x-rate-limit-reset", 0))
            current_time = int(time.time())
            wait_seconds = max(0, reset_time - current_time)
            wait_minutes = wait_seconds // 60
            wait_seconds_remainder = wait_seconds % 60
            error_msg = f"[WARNING] Rate limit reached. Please wait {wait_minutes} minutes and {wait_seconds_remainder} seconds before retrying."
            if logger:
                logger.warning(error_msg)
            return None, error_msg, wait_seconds
        else:
            error_msg = f"API呼び出し失敗: {response.text}"
            if logger:
                logger.error(error_msg)
            return None, error_msg, None


# コマンドライン実行用
if __name__ == "__main__":
    # ログ設定
    log_file = "view_tweets.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 50)
    logger.info("ツイート検索処理開始")
    
    # 検索ワード（固定値）
    query = "python"
    logger.info(f"検索ワード: {query}")

    use_mock = "--mock" in sys.argv
    logger.info(f"モックモード: {use_mock}")

    data, error, rate_limit_seconds = search_tweets(query, use_mock, logger)

    if error:
        logger.error(error)
        if rate_limit_seconds:
            logger.error(f"RATELIMIT_SECONDS:{rate_limit_seconds}")
            print(f"RATELIMIT_SECONDS:{rate_limit_seconds}")
        sys.exit(1)
    else:
        result_json = json.dumps(data, indent=4, ensure_ascii=False)
        logger.info("検索結果:")
        logger.info(result_json)
        print(result_json)
        
        # DBに登録
        logger.info("DBへの登録を開始します...")
        success, fail = save_tweets_to_db(data, logger)
        logger.info(f"DB登録結果 - 成功: {success}件, 失敗: {fail}件")
    
    logger.info("ツイート検索処理終了")
    logger.info("=" * 50)
