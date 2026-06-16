import sys
import json
import logging
import argparse

# Import functions from the module that contains API/DB logic
from view_tweets import search_tweets, save_tweets_to_db

# 標準出力/標準エラーのエンコーディングを UTF-8 に設定（Windows 対応）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass


def setup_logger(log_file="view_tweets.log"):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run tweet search and save results.")
    args = parser.parse_args()

    logger = setup_logger()
    logger.info("=" * 50)
    logger.info("ツイート検索処理開始")

    # モック判定は view_tweets.py 内で stub_tweet.json の存在をチェックします
    results, errors = search_tweets(logger)

    # エラー出力
    if errors:
        for error in errors:
            logger.error(error)

    # 結果処理
    if results:
        logger.info(f"検索完了: {len(results)}件のモニター結果を取得")
        for result in results:
            monitor = result['monitor']
            monitor_id = monitor['monitor_id']
            keywords = result['keywords']
            data = result['data']

            logger.info(f"--- モニターID {monitor_id} (キーワード: {keywords}) ---")

            # JSON出力
            result_json = json.dumps(data, indent=4, ensure_ascii=False)
            print(result_json)

            # DBに登録
            logger.info(f"DBへの登録を開始... (モニターID {monitor_id})")
            success, fail = save_tweets_to_db(data, monitor, logger)
            logger.info(f"DB登録結果 - 成功: {success}件, 失敗: {fail}件")
    else:
        logger.warning("取得できた検索結果がありません。")
        if errors:
            sys.exit(1)

    logger.info("ツイート検索処理終了")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
