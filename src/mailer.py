"""Transactional SMTP delivery; credentials stay in the server environment."""

import html
import logging
import os
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid, parseaddr


logger = logging.getLogger("aniverse.mail")


def smtp_settings():
    def setting(name, legacy="", default=""):
        return (os.environ.get(name) or os.environ.get(legacy) or default).strip()

    username = setting("SMTP_USERNAME", "QQ_MAIL_USERNAME")
    password = setting("SMTP_PASSWORD", "QQ_MAIL_AUTH_CODE")
    host = setting("SMTP_HOST", "QQ_SMTP_HOST", "smtp.qq.com")
    port = int(setting("SMTP_PORT", "QQ_SMTP_PORT", "465"))
    security = setting("SMTP_SECURITY", default="ssl" if port == 465 else "starttls").lower()
    sender = setting("SMTP_FROM_EMAIL", default=username)
    display_name = setting("SMTP_FROM_NAME", default="AniVerse")
    timeout = max(3, min(20, int(setting("SMTP_TIMEOUT", default="10"))))
    if not username or not password or "your_qq_number" in username or password.startswith("replace-with"):
        raise RuntimeError("SMTP credentials are not configured")
    if security not in {"ssl", "starttls"}:
        raise ValueError("SMTP_SECURITY must be ssl or starttls")
    if parseaddr(sender)[1] != sender or "@" not in sender:
        raise ValueError("SMTP_FROM_EMAIL must contain a single email address")
    return {"host": host, "port": port, "username": username, "password": password,
            "sender": sender, "display_name": display_name, "timeout": timeout,
            "security": security}


def send_code_email(target_email, code, validity_minutes=5, *, purpose="registration"):
    config = smtp_settings()
    test = purpose == "test"
    subject = "【AniVerse】邮件投递测试" if test else "【AniVerse】注册验证码"
    intro = "这是你在网站维护期间请求的测试邮件，无需注册或回复。" if test else "欢迎注册 AniVerse 动漫评分社区。"
    note = "此测试码仅用于核对邮件投递，不用于登录或注册。" if test else f"验证码 {validity_minutes} 分钟内有效。如非本人操作，请忽略本邮件。"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((config["display_name"], config["sender"]))
    message["To"] = target_email
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["Message-ID"] = make_msgid(domain=config["sender"].rsplit("@", 1)[1])
    message.set_content(f"{intro}\n\n{'测试码' if test else '您的注册验证码'}：{code}\n\n{note}\n\nAniVerse · https://aniverse.cloud\n")
    message.add_alternative(
        '<!doctype html><html lang="zh-CN"><body style="margin:0;padding:28px;background:#f5f5f8;color:#35303e;font-family:Arial,sans-serif">'
        '<div style="max-width:480px;margin:auto;background:white;padding:28px;border:1px solid #e6e1ec;border-radius:14px">'
        '<h1 style="font-size:23px;color:#6c59e8;margin:0 0 20px">AniVerse</h1>'
        f'<p style="line-height:1.8">{html.escape(intro)}</p>'
        f'<p style="font-size:32px;font-weight:bold;letter-spacing:8px;color:#6c59e8">{html.escape(str(code))}</p>'
        f'<p style="font-size:13px;line-height:1.8;color:#756780">{html.escape(note)}</p>'
        '<p style="font-size:12px;color:#9b8aab">发现 · 评价 · 分享</p></div></body></html>',
        subtype="html",
    )
    domain = target_email.rsplit("@", 1)[-1].lower()
    message_id = message["Message-ID"]
    for attempt in range(1, 3):
        smtp = None
        phase = "connect"
        accepted = False
        try:
            context = ssl.create_default_context()
            if config["security"] == "ssl":
                smtp = smtplib.SMTP_SSL(config["host"], config["port"], timeout=config["timeout"], context=context)
            else:
                smtp = smtplib.SMTP(config["host"], config["port"], timeout=config["timeout"])
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            phase = "login"
            smtp.login(config["username"], config["password"])
            phase = "send"
            refused = smtp.send_message(message, from_addr=config["sender"], to_addrs=[target_email])
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
            accepted = True
            logger.warning("mail_accepted message_id=%s recipient_domain=%s host=%s attempts=%s",
                           message_id, domain, config["host"], attempt)
            return {"message_id": message_id, "attempts": attempt, "status": "accepted"}
        except Exception as exc:
            smtp_code = getattr(exc, "smtp_code", None)
            transient = (isinstance(exc, OSError) and not isinstance(exc, smtplib.SMTPResponseException)) or (
                isinstance(smtp_code, int) and 400 <= smtp_code < 500
            )
            retry = attempt < 2 and phase != "send" and transient
            # Do not log credentials, verification codes, or full recipient addresses.
            logger.warning("mail_failed message_id=%s recipient_domain=%s phase=%s error_type=%s smtp_code=%s retry=%s",
                           message_id, domain, phase, type(exc).__name__, smtp_code, retry)
            if not retry:
                raise
            time.sleep(0.5)
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except Exception:
                    # A bad QUIT response cannot undo an already accepted message.
                    logger.warning("mail_connection_cleanup message_id=%s accepted=%s", message_id, accepted)
                finally:
                    try:
                        smtp.close()
                    except OSError:
                        pass
