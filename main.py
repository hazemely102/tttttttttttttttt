import requests
import re
import urllib.parse
import pycountry
import logging
import os
# from threading import Thread # لم نعد بحاجة لـ Thread

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes # لم نعد بحاجة لـ CallbackContext بشكل مباشر كثيرًا مع v20+
from telegram.constants import ParseMode, ChatAction

from flask import Flask, request # استيراد request من Flask

# --- إعدادات التسجيل ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO # يمكنك تغيير هذا إلى logging.DEBUG لرؤية المزيد من التفاصيل أثناء التطوير
)
logger = logging.getLogger(__name__)

# --- التوكن الخاص بالبوت ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical("!!! TELEGRAM_BOT_TOKEN environment variable not set!")
    # في بيئة الإنتاج، قد ترغب في الخروج أو رفع استثناء هنا
    # exit() or raise EnvironmentError("TELEGRAM_BOT_TOKEN not set")

# --- تهيئة تطبيق python-telegram-bot ---
# تأكد أن هذا يتم قبل تعريف مسارات Flask التي تستخدمه
if BOT_TOKEN:
    ptb_application = Application.builder().token(BOT_TOKEN).build()
else:
    ptb_application = None # أو تعامل مع هذا بشكل أفضل إذا لم يتم العثور على التوكن

# --- إعداد تطبيق Flask ---
# Gunicorn سيبحث عن هذا المتغير (أو الاسم الذي تحدده في أمر البدء)
flask_app = Flask(__name__) # اسم المتغير 'flask_app' مهم لأمر البدء gunicorn main:flask_app

# --- مسار Webhook لاستقبال التحديثات من تليجرام ---
@flask_app.route('/', methods=['POST']) # افترضنا أن الـ Webhook سيُرسل إلى المسار الجذر
async def webhook_handler():
    if not ptb_application:
        logger.error("Telegram application not initialized (BOT_TOKEN missing?)")
        return "Error: Bot not configured", 500

    logger.info("Webhook received")
    try:
        update_data = request.get_json(force=True)
        update = Update.de_json(data=update_data, bot=ptb_application.bot)
        logger.debug(f"Update content: {update}") # سجل محتوى التحديث إذا كان debug mode مفعل
        await ptb_application.process_update(update)
        return 'OK', 200
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True) # exc_info=True يضيف traceback للخطأ
        return 'Error processing update', 500

# --- مسار لفحص الصحة (Health Check) ولـ UptimeRobot ---
@flask_app.route('/', methods=['GET']) # نفس المسار الجذر ولكن لطلبات GET
def health_check():
    logger.info("Health check / UptimeRobot ping received")
    return "Bot is alive and processing webhooks!", 200

# --- دالة تهريب Markdown V2 (عامة) ---
def escape_markdown_v2(text: str) -> str:
    if not isinstance(text, str):
        return str(text)
    escape_chars = r'_*[]()~`>#+-.=|{}!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# --- دالة مساعدة لتحويل كود الدولة إلى اسم ---
def get_country_name_from_code(code):
    if not code or not isinstance(code, str) or len(code) != 2:
        return code
    try:
        country = pycountry.countries.get(alpha_2=code.upper())
        return country.name if country else code
    except Exception:
        return code

# --- دالة جلب معلومات تيك توك ---
def get_tiktok_user_info(username):
    if username.startswith('@'):
        username = username[1:]
    url = f"https://www.tiktok.com/@{username}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch page for '{username}'. Error: {e}")
        return {"error": f"فشل في جلب الصفحة لـ '{username}'. الخطأ: {e}"}

    html_content = response.text
    info = {}

    match = re.search(r'"uniqueId":"(.*?)"', html_content)
    if not match:
        if "Couldn't find this account" in html_content or "User not found" in html_content:
             return {"error": f"الحساب '{username}' غير موجود أو غير متاح."}
        else:
             logger.warning(f"Could not extract basic info for '{username}'. Page structure might have changed or access is blocked.")
             return {"error": f"لم يتمكن من استخراج المعلومات الأساسية لـ '{username}'. قد يكون هيكل الصفحة قد تغير أو تم حظر الوصول."}

    info['username'] = match.group(1)
    match = re.search(r'"nickname":"(.*?)"', html_content)
    info['full_name'] = match.group(1).encode().decode('unicode_escape') if match else "❌ غير موجود"

    match = re.search(r'"followerCount":(\d+)', html_content)
    info['followers'] = int(match.group(1)) if match else 0
    match = re.search(r'"heartCount":(\d+)', html_content)
    info['likes'] = int(match.group(1)) if match else 0
    match = re.search(r'"videoCount":(\d+)', html_content)
    info['videos'] = int(match.group(1)) if match else 0

    region_code = None
    matches = re.findall(r'"region":"(.*?)"', html_content)
    if matches:
        region_code = matches[-1]
    info['region'] = get_country_name_from_code(region_code) if region_code else "❌ غير موجود"

    match = re.search(r'"avatarLarger":"(.*?)"', html_content)
    info['profile_picture'] = match.group(1).replace('\\u002F', '/') if match else None

    match = re.search(r'"signature":"(.*?)"', html_content)
    try:
        info['bio'] = match.group(1).encode('latin-1', 'backslashreplace').decode('unicode-escape').replace('\\n', '\n') if match else "❌ غير موجود"
    except Exception:
         info['bio'] = match.group(1).replace('\\n', '\n').replace('\\u002F', '/') if match else "❌ غير موجود"

    match = re.search(r'"followingCount":(\d+)', html_content)
    info['following'] = int(match.group(1)) if match else 0
    language_matches = re.findall(r'"language":"(.*?)"', html_content)
    info['language'] = language_matches[-1] if language_matches else "❌ غير موجود"
    match = re.search(r'"verified":(true|false)', html_content)
    info['verified'] = match.group(1) == 'true' if match else False
    match = re.search(r'"privateAccount":(true|false)', html_content)
    info['privateAccount'] = match.group(1) == 'true' if match else False
    info['profile_url'] = url

    social_links = []
    bio_text = info.get('bio', "")
    if bio_text == "❌ غير موجود": bio_text = ""

    direct_links_in_bio = re.findall(r'(https?://[^\s\'"<>]+|www\.[^\s\'"<>]+)', bio_text)
    for link in direct_links_in_bio:
        if not any(link in s for s in social_links):
             social_links.append(f"🔗 من البايو: {link}")

    bio_link_matches = re.findall(r'"bioLink":\s*\{\s*"link":\s*"([^"]+)"', html_content)
    for link in bio_link_matches:
        clean_link = link.replace('\\u002F', '/')
        if not any(clean_link in s for s in social_links):
            social_links.append(f"🔗 رابط البايو: {clean_link}")

    target_link_matches = re.findall(r'href="(https://www\.tiktok\.com/link/v2\?[^"]*?target=([^"&]+))"', html_content)
    for full_url, target in target_link_matches:
        try:
            target_decoded = urllib.parse.unquote(target)
        except Exception:
            target_decoded = target
        text_match = re.search(rf'href="{re.escape(full_url)}"[^>]*>.*?<span[^>]*>([^<]+)</span>', html_content, re.DOTALL)
        link_text = text_match.group(1).strip() if text_match else target_decoded
        link_entry = f"🔗 {link_text}: {target_decoded}"
        if not any(target_decoded in s for s in social_links):
            social_links.append(link_entry)

    social_patterns = {
        'Instagram': r'[iI][gG][\s:]*@?([a-zA-Z0-9._]+)',
        'Snapchat': r'([sS][cC]|[sS]napchat)[\s:]*@?([a-zA-Z0-9._]+)',
        'Twitter/X': r'([tT]witter|[xX])[\s:]*@?([a-zA-Z0-9._]+)',
        'YouTube': r'([yY][tT]|[yY]outube)[\s:]*@?([a-zA-Z0-9._]+)',
        'Telegram': r'[tT]elegram[\s:]*@?([a-zA-Z0-9._]+)',
        'Facebook': r'[fF][bB][\s:]*@?([a-zA-Z0-9._]+)'
    }
    for platform, pattern in social_patterns.items():
        match = re.search(pattern, bio_text)
        if match:
            username_match = match.group(1) if len(match.groups()) == 1 else match.group(2)
            social_link_text = f"📱 {platform}: {username_match}"
            is_duplicate = False
            for existing_link in social_links:
                if username_match in existing_link:
                   is_duplicate = True
                   break
            if not is_duplicate:
                social_links.append(social_link_text)

    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', bio_text)
    if email_match:
        email = email_match.group(0)
        if not any(email in s for s in social_links):
            social_links.append(f"📧 ايميل: {email}")
    info['social_links'] = social_links
    return info

# --- دالة لتنسيق المعلومات لإرسالها عبر تليجرام ---
def format_user_info_for_telegram(info: dict) -> str:
    if "error" in info:
        return f"❌ خطأ: {info['error']}"

    username_escaped = escape_markdown_v2(info['username'])
    full_name_escaped = escape_markdown_v2(info['full_name'])
    region_escaped = escape_markdown_v2(info['region'])
    language_escaped = escape_markdown_v2(info['language'])

    followers_escaped = escape_markdown_v2(f"{info['followers']:,}")
    following_escaped = escape_markdown_v2(f"{info['following']:,}")
    likes_escaped = escape_markdown_v2(f"{info['likes']:,}")
    videos_escaped = escape_markdown_v2(str(info['videos']))

    message_parts = [
        f"*👤 اسم المستخدم:* `{username_escaped}`",
        f"*📛 الاسم الكامل:* {full_name_escaped}",
        f"*✅ موثق:* {'نعم' if info.get('verified', False) else 'لا'}",
        f"*🔒 حساب خاص:* {'نعم' if info.get('privateAccount', False) else 'لا'}",
        f"*👥 المتابعون:* {followers_escaped}",
        f"*➡️ يتابع:* {following_escaped}",
        f"*❤️ الإعجابات:* {likes_escaped}",
        f"*🎥 الفيديوهات:* {videos_escaped}",
        f"*🌍 المنطقة:* {region_escaped}",
        f"*🈯 اللغة:* {language_escaped}",
        f"*🔗 رابط الملف الشخصي:* [{escape_markdown_v2('اضغط هنا')}]({info['profile_url']})",
        "\n*📝 البايو:*",
    ]

    bio_escaped = f"   {escape_markdown_v2('(لا يوجد بايو)')}"
    if info.get('bio') and info['bio'] != "❌ غير موجود":
        bio_lines = info['bio'].splitlines()
        if bio_lines:
            bio_escaped = "\n".join([f"   {escape_markdown_v2(line)}" for line in bio_lines])
        else:
             bio_escaped = f"   {escape_markdown_v2('(فارغ)')}"
    message_parts.append(bio_escaped)

    if info.get('social_links'):
        message_parts.append(f"\n*{escape_markdown_v2('🔗 روابط اجتماعية وإشارات:')}*")
        for link_text_original in info['social_links']:
            link_match = re.search(r'(https?://[^\s]+|www\.[^\s]+)', link_text_original)
            if link_match:
                actual_link_url = link_match.group(0)
                link_display_text = link_text_original.replace(actual_link_url, "").strip()
                if not link_display_text: link_display_text = actual_link_url
                message_parts.append(f"  \\- {escape_markdown_v2(link_display_text)}: [{escape_markdown_v2(actual_link_url)}]({actual_link_url})")
            else:
                 message_parts.append(f"  \\- {escape_markdown_v2(link_text_original)}")
    else:
        message_parts.append(f"\n*{escape_markdown_v2('🔗 روابط اجتماعية وإشارات:')}* {escape_markdown_v2('(لم يتم العثور على شيء)')}")

    return "\n".join(message_parts)

# --- معالجات أوامر البوت (تضاف إلى ptb_application) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received /start command from user {update.effective_user.id}")
    await update.message.reply_text(
        "أهلاً بك! 👋\n"
        "أرسل لي اسم مستخدم تيك توك (مثال: `@username` أو `username`) وسأجلب لك معلومات عنه.\n\n"
        "مطور البوت: @MyTikInfoBot" # استبدل هذا إذا أردت
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: # تحقق إضافي
        logger.warning("Received an update without a message or text.")
        return

    username_input = update.message.text.strip()
    logger.info(f"Received message from user {update.effective_user.id}: {username_input}")

    if not username_input:
        await update.message.reply_text("الرجاء إرسال اسم مستخدم صالح.")
        return

    escaped_username_input = escape_markdown_v2(username_input)
    loading_message_text = f"⏳ جاري جلب المعلومات لـ '{escaped_username_input}'\\.\\.\\."

    processing_message = None
    try:
        processing_message = await update.message.reply_text(loading_message_text, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e_loading_md:
        logger.error(f"Error sending loading message with Markdown: {e_loading_md}. Trying plain.")
        try:
            processing_message = await update.message.reply_text(f"⏳ جاري جلب المعلومات لـ '{username_input}'...")
        except Exception as e_loading_plain:
            logger.error(f"FATAL: Could not send even plain loading message: {e_loading_plain}")
            await update.message.reply_text("⚠️ حدث خطأ فادح أثناء محاولة بدء طلبك. يرجى المحاولة مرة أخرى لاحقًا.")
            return

    if not processing_message:
        logger.error("FATAL: processing_message is None after attempting to send. This should not happen.")
        await update.message.reply_text("⚠️ حدث خطأ داخلي غير متوقع (فشل إرسال رسالة الانتظار). يرجى المحاولة مرة أخرى.")
        return

    user_info = get_tiktok_user_info(username_input)
    formatted_message = format_user_info_for_telegram(user_info)
    
    try:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=processing_message.message_id,
            text=formatted_message,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True
        )
    except Exception as e_edit_md:
        logger.error(f"Error editing message with Markdown: {e_edit_md}. Falling back to plain text.")
        
        plain_text_message = formatted_message
        chars_to_clean = r'_*[]()~`>#+-.=|{}!' 
        for char_esc in chars_to_clean:
            plain_text_message = plain_text_message.replace(f'\\{char_esc}', char_esc) 
        for char_md in ['*', '`', '~', '[', ']', '(', ')']:
            plain_text_message = plain_text_message.replace(char_md, '')
            
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_message.message_id,
                text=plain_text_message,
                disable_web_page_preview=True
            )
        except Exception as e_edit_plain:
            logger.error(f"Error editing plain text message: {e_edit_plain}. Sending new message.")
            await update.message.reply_text(plain_text_message, disable_web_page_preview=True)


    if "error" not in user_info and user_info.get('profile_picture'):
        pic_url = user_info['profile_picture']
        caption_plain = f"صورة الملف الشخصي لـ @{user_info['username']}" 
        caption_md = f"صورة الملف الشخصي لـ @{escape_markdown_v2(user_info['username'])}"
        try:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
            await update.message.reply_photo(
                photo=pic_url,
                caption=caption_md,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e_photo_md:
            logger.warning(f"Failed to send photo with Markdown caption: {e_photo_md}. Retrying with plain caption.")
            try:
                 await update.message.reply_photo(
                    photo=pic_url,
                    caption=caption_plain
                )
            except Exception as e_photo_plain:
                logger.error(f"Error sending photo with plain caption: {e_photo_plain}")
                await update.message.reply_text(f"❌ فشل في إرسال الصورة. التعليق: {caption_plain}")

# --- إضافة المعالجات إلى تطبيق ptb ---
if ptb_application:
    ptb_application.add_handler(CommandHandler("start", start_command))
    ptb_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Telegram handlers added.")
else:
    logger.warning("Telegram application not initialized, handlers not added.")


# --- الجزء الخاص بتشغيل Flask (عندما لا يكون Gunicorn هو المشغل) ---
# هذا الجزء مفيد للاختبار المحلي فقط. Gunicorn سيتجاهله.
if __name__ == '__main__':
    if not BOT_TOKEN:
        print("!!! TELEGRAM_BOT_TOKEN environment variable not set. Cannot run Flask server for bot. !!!")
    elif not ptb_application:
        print("!!! Telegram application (ptb_application) not initialized. Cannot run Flask server. !!!")
    else:
        # هذا لتشغيل خادم تطوير Flask إذا شغلت الملف مباشرة `python main.py`
        # Render لن يستخدم هذا الجزء، بل سيستخدم أمر gunicorn.
        logger.info("Starting Flask development server for local testing...")
        # ptb_application.initialize() # تأكد من تهيئة التطبيق إذا لزم الأمر (عادةً ما يتم تلقائيًا)
        
        # قبل تشغيل Flask، يمكنك تعيين الـ webhook محليًا إذا كنت تستخدم ngrok للاختبار
        # loop = asyncio.get_event_loop()
        # if loop.is_running():
        # logger.info("Event loop is running. Setting up bot for webhook.")
        # await ptb_application.bot.set_webhook(url="YOUR_NGROK_URL_HERE") # <--- استبدل هذا
        # else:
        # logger.info("Event loop not running. Initializing application for webhook.")
        # await ptb_application.initialize()
        # await ptb_application.bot.set_webhook(url="YOUR_NGROK_URL_HERE") # <--- استبدل هذا

        # للحصول على البورت من متغيرات البيئة أو استخدام قيمة افتراضية
        port = int(os.environ.get("PORT", 8080)) # Render سيوفر متغير PORT
        flask_app.run(host='0.0.0.0', port=port, debug=True) # debug=True للاختبار المحلي فقط
        logger.info(f"Flask development server running on http://0.0.0.0:{port}")
