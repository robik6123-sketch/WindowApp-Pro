import smtplib
from email.mime.text import MIMEText
import urllib.parse

def generate_report_text(payload, validation_result, calc_result):
    width = payload.get('width', 1000)
    height = payload.get('height', 1000)
    profile = payload.get('profile', 'Невідомий профіль')
    
    text = (
        "🏗 ТЕХНІЧНИЙ ЗВІТ (WINDOW APP PRO)\n"
        "====================================\n\n"
        f"📋 Параметри:\n"
        f"• Габарити: {width} x {height} мм\n"
        f"• Профіль: {profile}\n"
    )
    
    if calc_result and calc_result.get("status") == "success":
        cost = calc_result.get("cost_details", {}).get("total", 0.0)
        weight = calc_result.get("weight", 0.0)
        area = calc_result.get("area", 0.0)
        
        # Отримуємо ціни з гарантованим значенням 0
        net_price = calc_result.get("net_price", 0.0)
        vat_amount = calc_result.get("vat_amount", 0.0)
        
        text += (
            f"• Площа: {area} м² | Вага: ~{weight} кг\n"
            f"• Фурнітура: {', '.join(calc_result.get('hardware', ['Базова']))}\n"
        )
        
        # Фінансовий блок (вище для месенджерів)
        text += (
            f"\n💰 ФІНАНСОВІ ПОКАЗНИКИ:\n"
            f"------------------------------------\n"
            f"Чиста вартість (Net): {net_price} грн\n"
            f"**ПДВ (або податок): {vat_amount} грн**\n"
            f"**РАЗОМ ДО СПЛАТИ: {cost} грн**\n"
        )
        
        if payload.get("sill_width", 0) > 0:
            text += f"• Відлив: {payload.get('sill_length', 0)} x {payload.get('sill_width', 0)} мм\n"
        if payload.get("window_board", "none") != "none":
            text += f"• Підвіконня: {payload.get('window_board_name', 'Стандарт')}\n"
    
    text += "\n🛡 ВИСНОВОК ТЕХНОЛОГА:\n"
    if validation_result.get("valid"):
        text += "✅ Конструкція відповідає технічним нормам.\n"
    else:
        text += "⚠️ УВАГА! Порушення:\n"
        for msg in validation_result.get("messages", []):
            text += f"❌ {msg}\n"
            
    if calc_result and calc_result.get("legal_reference"):
        text += f"\nℹ️ Примітка: {calc_result.get('legal_reference')}\n"

    return text

def send_technical_report(payload, validation_result, calc_result, channel='telegram', email_config=None):
    report_text = generate_report_text(payload, validation_result, calc_result)
    
    if channel == 'email_smtp':
        if not email_config: return {"status": "error", "message": "No SMTP config", "text": report_text}
        try:
            msg = MIMEText(report_text, 'plain', 'utf-8')
            msg['Subject'] = f'WindowApp: {payload.get("profile", "Order")}'
            msg['From'] = email_config.get('login')
            msg['To'] = email_config.get('to')
            with smtplib.SMTP(email_config.get('smtp_server'), email_config.get('smtp_port', 587)) as s:
                s.starttls()
                s.login(email_config.get('login'), email_config.get('password'))
                s.send_message(msg)
            return {"status": "success", "message": "Sent", "text": report_text}
        except Exception as e:
            return {"status": "error", "message": str(e), "text": report_text}

    elif channel in ['telegram', 'viber', 'whatsapp', 'email']:
        encoded_text = urllib.parse.quote(report_text)
        links = {
            'telegram': f"tg://msg?text={encoded_text}",
            'viber': f"viber://forward?text={encoded_text}",
            'whatsapp': f"https://api.whatsapp.com/send?text={encoded_text}",
            'email': f"mailto:?subject=WindowApp&body={encoded_text}"
        }
        return {"status": "success", "share_link": links[channel], "text": report_text}
    return {"status": "error", "message": "Unknown channel", "text": report_text}
