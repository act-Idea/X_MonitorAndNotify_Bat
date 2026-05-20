import os
import smtplib
import psycopg2
import psycopg2.extras
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

# SMTP設定を環境変数から取得
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", 25))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")

FROM_ADDRESS = os.getenv("EMAIL_FROM", SMTP_USER or "no-reply@example.com")

print(f"SMTP_HOST: {SMTP_HOST}")
print(f"SMTP_PORT: {SMTP_PORT}")
print(f"SMTP_USER: {SMTP_USER}")
print(f"SMTP_USE_TLS: {SMTP_USE_TLS}")
print(f"FROM_ADDRESS: {FROM_ADDRESS}")

def get_db_connection():
    """DB接続を取得"""
    return psycopg2.connect(os.getenv("SUPABASE_DB_URL"))


def send_email(to: str, subject: str, body: str):
    """シンプルなテキストメール送信"""
    msg = EmailMessage()
    msg["From"] = FROM_ADDRESS
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
        if SMTP_USE_TLS:
            smtp.starttls()
        if SMTP_USER and SMTP_PASSWORD:
            smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)

    print(f"メールを {to} に送信しました。")


def send_monitor_notification(monitor_id: int):
    """monitor_id を受け取り、monitor_settings からメール送信する"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT *
            FROM monitor_settings
            WHERE monitor_id = %s
            """,
            (monitor_id,)
        )
        monitor_data = cur.fetchone()
        cur.close()
        conn.close()

        if monitor_data and monitor_data["notify_email"]:
            to = monitor_data["notify_email"]
            subject = f"X自動検索・通知システム {monitor_data['monitor_name']}"
            body = f"モニター「{monitor_data['monitor_name']}」の通知です。"

            send_email(to, subject, body)
            print("通知メール送信成功！")
        else:
            print(f"monitor_id={monitor_id} のデータが無いか notify_email が未設定です。")

    except Exception as e:
        print(f"メール送信エラー: {e}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("使い方: python monitor_notifier.py <monitor_id>")
        sys.exit(1)

    monitor_id = int(sys.argv[1])
    send_monitor_notification(monitor_id)