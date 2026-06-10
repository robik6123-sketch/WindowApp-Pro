from flask import Flask, request, jsonify, render_template
from calculator import WindowCalculator
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
        # The new calculate_project method handles the entire complex payload
        # mapping proportions, sections, custom prices, and sills.
        result = calc.calculate_project(data)
        return jsonify(result)
        
    except ValueError as e:
        return jsonify({"status": "error", "error": str(e)}), 400
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
            calc_result = calc.calculate_project(data)
            
        res = send_technical_report(data, validation_result, calc_result, channel, email_config)
        return jsonify(res)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
