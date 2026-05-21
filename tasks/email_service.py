import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy import text

def send_email_notification(to_email, keyword, event_title, event_url):
    sender_email = os.getenv("SMTP_EMAIL", "")
    sender_password = os.getenv("SMTP_PASSWORD", "")
    if not sender_email or not sender_password:
        print("未設定 SMTP 環境變數，跳過寄信。")
        return
        
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = to_email
    msg['Subject'] = f"【OmniTicket 新活動通知】{keyword}"
    body = f"您好，\n\n系統發現符合您訂閱關鍵字「{keyword}」的新活動上架：\n\n【{event_title}】\n👉 搶票連結：{event_url}\n\n請盡快前往 OmniTicket 平台查看與搶票！"
    msg.attach(MIMEText(body, 'plain'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        print(f"成功寄信通知: {to_email}")
    except Exception as e:
        print(f"發送信件失敗: {e}")

def notify_subscribers(db, new_titles):
    try:
        subs = db.execute(text("SELECT email, keywords FROM subscriptions WHERE email IS NOT NULL AND email != ''")).fetchall()
        for email, kw_json in subs:
            if not kw_json: continue
            kws = json.loads(kw_json) if isinstance(kw_json, str) else kw_json
            for title in new_titles:
                for kw in kws:
                    if kw.lower() in title.lower():
                        # 從資料庫反查這筆活動的購票網址
                        event_record = db.execute(text("SELECT external_url FROM events WHERE title = :title LIMIT 1"), {"title": title}).fetchone()
                        url = event_record[0] if event_record else "請前往系統查看"
                        send_email_notification(email, kw, title, url)
                        break
    except Exception as e:
        print(f"撈取訂閱者失敗: {e}")