import os
import smtplib
import psycopg2
import psycopg2.extras
from email.message import EmailMessage
from dotenv import load_dotenv

# .env 読み込み
load_dotenv()

# SMTP設定
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", 25))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")

# 送信元メールアドレス
FROM_ADDRESS = os.getenv("EMAIL_FROM", SMTP_USER or "no-reply@example.com")

print(f"SMTP_HOST: {SMTP_HOST}")
print(f"SMTP_PORT: {SMTP_PORT}")
print(f"SMTP_USER: {SMTP_USER}")
print(f"SMTP_USE_TLS: {SMTP_USE_TLS}")
print(f"FROM_ADDRESS: {FROM_ADDRESS}")

def get_db_connection():
    """DB接続"""
    return psycopg2.connect(os.getenv("SUPABASE_DB_URL"))

def send_email(to: str, subject: str, body: str):
    """メール送信処理"""
    msg = EmailMessage()
    msg["From"] = FROM_ADDRESS
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    # SMTP サーバーへ接続
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
        if SMTP_USE_TLS:
            smtp.starttls()
        if SMTP_USER and SMTP_PASSWORD:
            smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)

    print(f"メールを {to} に送信しました。")

def send_monitor_notification():
    """
    検知結果の通知処理

    1. monitor_results から検知結果を取得
    2. notifications に登録済みか確認
    3. 未登録の場合、メール送信
    4. notifications に登録
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # monitor_results から24時間以内の検知結果を取得
        cur.execute(
            """
            SELECT *
            FROM monitor_results
            INNER JOIN monitor_settings
            ON monitor_results.monitor_id = monitor_settings.monitor_id
            WHERE detected_at >= NOW() - INTERVAL '1 day'
            """
        )
        monitor_results = cur.fetchall()

        if not monitor_results:
            print("monitor_results が0件（通知対象なし）")
            return

        # 検知結果を1件ずつ処理
        for row in monitor_results:
            result_id = row["result_id"]

            # notifications に登録済みか確認
            cur.execute(
                """
                SELECT 1
                FROM notifications
                WHERE result_id = %s
                LIMIT 1
                """,
                (result_id,)
            )
            notified = cur.fetchone()

            if notified:
                print(f"result_id={result_id} は既に通知済みです。")
                continue

            # メール送信
            if row["notify_email"]:
                to = row["notify_email"]
                subject = f"X自動検索・通知システム {row['monitor_name']}"

                hashtags = " ".join(f"#{tag}" for tag in row["hashtags"])

                body = (
                    f"モニター「{row['monitor_name']}」で条件に合う投稿が見つかりましたのでお知らせします。\n"
                    f"\n"
                    f"■ 投稿者\n{row['user_handle']}\n"
                    f"\n"
                    f"■ 投稿内容\n{row['content']}\n"
                    f"\n"
                    f"■ ハッシュタグ\n{hashtags}\n"
                    f"\n"
                    f"■ 投稿リンク\n{row['post_url']}\n"
                    f"\n"
                    f"■ 投稿日時\n{row['posted_at']}\n"
                    f"\n"
                    f"■ 検知日時\n{row['detected_at']}\n"
                    f"\n"
                    f"本メールは自動送信されています。"
                )

                send_email(to, subject, body)
                print("通知メール送信成功！")

            else:
                print(f"monitor_id={row['monitor_id']} の notify_email が未設定です。")
                continue

            # notifications に通知履歴を登録
            cur.execute(
                """
                INSERT INTO notifications (user_id, monitor_id, result_id, email)
                VALUES (%s, %s, %s, %s)
                """,
                (row['user_id'], row['monitor_id'], result_id, row['notify_email'])
            )
            conn.commit()
            print(f"通知履歴登録完了 result_id={result_id}")

        cur.close()
        conn.close()

    except Exception as e:
        print(f"[ERROR] メール送信エラー: {e}")

if __name__ == "__main__":
    send_monitor_notification()
