from flask import Flask, request, jsonify, render_template
from calculator import (
    WindowCalculator,
    CalculatorPricingError,
    UnknownMaterialError,
    MissingResolvedPriceError
)
from pricing_context_provider import get_default_pricing_context
from reporter import send_technical_report

app = Flask(__name__)
calc = WindowCalculator()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/calculate', methods=['POST'])
def calculate():
    try:
        data = request.json
        pricing_context = get_default_pricing_context(calc.materials)
        result = calc.calculate_project(data, pricing_context)
        return jsonify(result)
        
    except MissingResolvedPriceError as e:
        return jsonify({"status": "error", "error": "Внутрішня помилка розрахунку ціни"}), 500
    except UnknownMaterialError as e:
        return jsonify({"status": "error", "error": "Невідомий матеріал або конфігурація"}), 400
    except CalculatorPricingError as e:
        return jsonify({"status": "error", "error": "Помилка конфігурації калькулятора"}), 500
    except Exception as e:
        return jsonify({"status": "error", "error": f"Внутрішня помилка сервера: {str(e)}"}), 500

@app.route('/api/report', methods=['POST'])
def report():
    try:
        data = request.json
        channel = data.get('channel', 'telegram')
        email_config = data.get('email_config')
        
        # Отримуємо результати від Агента-Технолога
        validation_result = calc.validate_order(data)
        
        # Рахуємо, якщо пройшло валідацію
        calc_result = None
        if validation_result["valid"]:
            pricing_context = get_default_pricing_context(calc.materials)
            calc_result = calc.calculate_project(data, pricing_context)
            
        res = send_technical_report(data, validation_result, calc_result, channel, email_config)
        return jsonify(res)
    except MissingResolvedPriceError as e:
        return jsonify({"status": "error", "error": "Внутрішня помилка розрахунку ціни"}), 500
    except UnknownMaterialError as e:
        return jsonify({"status": "error", "error": "Невідомий матеріал або конфігурація"}), 400
    except CalculatorPricingError as e:
        return jsonify({"status": "error", "error": "Помилка конфігурації калькулятора"}), 500
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
