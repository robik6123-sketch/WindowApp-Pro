import json
from calculator import WindowCalculator
from pricing_context_provider import get_default_pricing_context
from reporter import send_technical_report

def run_poc():
    calc = WindowCalculator()
    
    payload = {
        "width": 2000.0,
        "height": 1000.0,
        "profile": "REHAU_Euro_70",
        "glass": "glass_24",
        "h_sections": 1,
        "panels": [
            {"type": "turn", "proportion": 100.0}
        ]
    }
    
    # 1. Запуск валідатора (Агент-Технолог)
    validation_result = calc.validate_order(payload)
    
    # 2. Виконання розрахунку (запускається тільки якщо валідно)
    calc_result = None
    if validation_result["valid"]:
        pricing_context = get_default_pricing_context(calc.materials)
        calc_result = calc.calculate_project(payload, pricing_context)
        
    # 3. Генерація звітів
    res_tg = send_technical_report(payload, validation_result, calc_result, channel='telegram')
    res_viber = send_technical_report(payload, validation_result, calc_result, channel='viber')
    res_whatsapp = send_technical_report(payload, validation_result, calc_result, channel='whatsapp')
    
    print("=== ТЕКСТ ТЕХНІЧНОГО ЗВІТУ ===")
    print(res_tg.get('text'))
    print("==============================")
    print("Посилання Telegram: " + res_tg.get('share_link', ''))
    print("Посилання Viber:    " + res_viber.get('share_link', ''))
    print("Посилання WhatsApp: " + res_whatsapp.get('share_link', ''))

if __name__ == "__main__":
    run_poc()
