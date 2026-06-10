from fpdf import FPDF
import datetime
import io
import base64

class CartQuotePDF(FPDF):
    def __init__(self):
        super().__init__()
        self.add_font("Roboto", style="", fname="static/Roboto-Regular.ttf")
        self.add_font("Roboto", style="B", fname="static/Roboto-Bold.ttf")
        self.add_font("Roboto", style="I", fname="static/Roboto-Italic.ttf")
        self.primary_col = (0, 82, 204)
        self.bg_col = (240, 245, 255)

    def header(self):
        self.set_font('Roboto', 'B', 20)
        self.set_text_color(*self.primary_col)
        self.cell(0, 10, text='WindowApp Pro v2.6', new_x="LMARGIN", new_y="NEXT", align='L')
        self.set_font('Roboto', 'I', 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 5, text='Професійна система розрахунку віконних конструкцій', new_x="LMARGIN", new_y="NEXT", align='L')
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y()+2, 200, self.get_y()+2)
        self.ln(7)

    def footer(self):
        self.set_y(-15)
        self.set_font('Roboto', 'I', 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, text=f'Згенеровано автоматично - Сторінка {self.page_no()} - {datetime.datetime.now().year}', align='C')

def generate_cart_pdf(cart_data):
    pdf = CartQuotePDF()
    pdf.add_page()
    
    order_id = cart_data.get('order_id', 'N/A')
    date_str = datetime.datetime.now().strftime('%d.%m.%Y %H:%M')
    
    pdf.set_font('Roboto', 'B', 14)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, text=f"Комерційна пропозиція №{order_id}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font('Roboto', '', 10)
    pdf.cell(0, 6, text=f"Дата видачі: {date_str}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    items = cart_data.get('items', [])
    
    grand_total = 0
    total_area = 0
    total_weight = 0
    total_perimeter = 0

    for idx, item in enumerate(items):
        inp = item.get('input', {})
        res = item.get('result', {})
        metrics = res.get('metrics', {})
        cost = res.get('cost_details', {})
        
        grand_total += cost.get('total', 0)
        total_area += metrics.get('area', 0)
        total_weight += metrics.get('weight', 0)
        total_perimeter += metrics.get('perimeter', 0)

        # Item Header
        pdf.set_font('Roboto', 'B', 12)
        pdf.set_fill_color(*pdf.bg_col)
        pdf.cell(0, 10, text=f" Конструкція №{idx+1}", new_x="LMARGIN", new_y="NEXT", align='L', fill=True)
        pdf.ln(2)

        start_y = pdf.get_y()
        
        # 2D Image (Front)
        images = inp.get('images', {})
        img_w = 80
        if images.get('front'):
            try:
                img_data = base64.b64decode(images['front'].split(',')[1])
                img_buf = io.BytesIO(img_data)
                pdf.image(img_buf, x=10, y=start_y, w=img_w)
                pdf.set_xy(10, start_y + (img_w * 0.75) + 2)
                pdf.set_font('Roboto', 'I', 8)
                pdf.cell(img_w, 5, text="Вигляд зсередини", align='C')
            except: pass

        # Specs
        table_x = 10 + img_w + 10
        pdf.set_xy(table_x, start_y)
        pdf.set_font('Roboto', 'B', 9)
        pdf.cell(90, 6, text="Характеристики", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")
        
        specs = [
            ("Профіль", inp.get('profile', 'Стандарт')),
            ("Склопакет", inp.get('glass', 'Стандарт')),
            ("Колір", inp.get('color', 'Білий')),
            ("Розміри (Ш x В)", f"{inp.get('width', 0)} x {inp.get('height', 0)} мм")
        ]
        
        for k, v in specs:
            pdf.set_xy(table_x, pdf.get_y())
            pdf.set_font('Roboto', 'B', 8)
            pdf.cell(30, 6, text=f" {k}:", border=1)
            pdf.set_font('Roboto', '', 8)
            pdf.cell(60, 6, text=f" {v}", border=1, new_x="LMARGIN", new_y="NEXT")

        # Metrics
        pdf.set_xy(table_x, pdf.get_y() + 2)
        pdf.set_font('Roboto', 'B', 9)
        pdf.cell(90, 6, text="Інженерні дані", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")
        
        m_data = [
            ("Площа", f"{metrics.get('area', 0)} м²"),
            ("Периметр", f"{metrics.get('perimeter', 0)} м"),
            ("Вага", f"{metrics.get('weight', 0)} кг")
        ]
        for k, v in m_data:
            pdf.set_xy(table_x, pdf.get_y())
            pdf.set_font('Roboto', 'B', 8)
            pdf.cell(30, 6, text=f" {k}:", border=1)
            pdf.set_font('Roboto', '', 8)
            pdf.cell(60, 6, text=f" {v}", border=1, new_x="LMARGIN", new_y="NEXT")
            
        # Price
        pdf.set_xy(table_x, pdf.get_y() + 2)
        pdf.set_font('Roboto', 'B', 10)
        pdf.set_text_color(*pdf.primary_col)
        pdf.cell(90, 8, text=f" Вартість: {cost.get('total', 0):,.2f} грн", border=1, new_x="LMARGIN", new_y="NEXT", align='R')
        pdf.set_text_color(0,0,0)

        end_y = max(pdf.get_y(), start_y + (img_w * 0.75) + 10)
        pdf.set_y(end_y + 5)
        
        if idx < len(items) - 1 and pdf.get_y() > 200:
            pdf.add_page()
            
    # Extras Table
    pdf.add_page()
    pdf.set_font('Roboto', 'B', 12)
    pdf.set_fill_color(*pdf.bg_col)
    pdf.cell(0, 10, text=" Додаткова комплектація (Всі вікна)", new_x="LMARGIN", new_y="NEXT", align='L', fill=True)
    pdf.ln(5)
    
    pdf.set_font('Roboto', 'B', 9)
    pdf.cell(80, 8, text=" Найменування", border=1)
    pdf.cell(40, 8, text=" Розміри", border=1)
    pdf.cell(30, 8, text=" Конструкція №", border=1)
    pdf.cell(40, 8, text=" Гарантія", border=1, new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font('Roboto', '', 9)
    has_extras = False
    for idx, item in enumerate(items):
        inp = item.get('input', {})
        if inp.get('sill_width', 0) > 0:
            has_extras = True
            pdf.cell(80, 8, text="Відлив", border=1)
            pdf.cell(40, 8, text=f"{inp.get('sill_length', 0)}x{inp.get('sill_width', 0)}", border=1)
            pdf.cell(30, 8, text=str(idx+1), border=1, align='C')
            pdf.cell(40, 8, text="Надається", border=1, new_x="LMARGIN", new_y="NEXT")
        if inp.get('window_board', 'none') != 'none':
            has_extras = True
            pdf.cell(80, 8, text=f"Підвіконня ({inp.get('window_board')})", border=1)
            pdf.cell(40, 8, text=f"{inp.get('window_board_length', 0)}x{inp.get('window_board_depth', 0)}", border=1)
            pdf.cell(30, 8, text=str(idx+1), border=1, align='C')
            pdf.cell(40, 8, text="Надається", border=1, new_x="LMARGIN", new_y="NEXT")
        for p in inp.get('panels', []):
            if p.get('mosquito'):
                has_extras = True
                pdf.cell(80, 8, text="Москітна сітка", border=1)
                pdf.cell(40, 8, text="За розміром стулки", border=1)
                pdf.cell(30, 8, text=str(idx+1), border=1, align='C')
                pdf.cell(40, 8, text="Надається", border=1, new_x="LMARGIN", new_y="NEXT")
                break
                
    if not has_extras:
        pdf.cell(190, 8, text=" Немає додаткових елементів", border=1, align='C', new_x="LMARGIN", new_y="NEXT")
        
    pdf.ln(10)
    
    # Grand Totals
    pdf.set_font('Roboto', 'B', 12)
    pdf.cell(0, 10, text=" ПІДСУМКИ", new_x="LMARGIN", new_y="NEXT", align='L', fill=True)
    pdf.ln(5)
    
    pdf.set_font('Roboto', 'B', 10)
    pdf.cell(47.5, 8, text=" Кількість конструкцій", border=1, align='C')
    pdf.cell(47.5, 8, text=" Загальна площа", border=1, align='C')
    pdf.cell(47.5, 8, text=" Загальна вага", border=1, align='C')
    pdf.cell(47.5, 8, text=" Загальний периметр", border=1, align='C', new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font('Roboto', '', 10)
    pdf.cell(47.5, 8, text=f"{len(items)} шт.", border=1, align='C')
    pdf.cell(47.5, 8, text=f"{total_area:.4f} м²", border=1, align='C')
    pdf.cell(47.5, 8, text=f"{total_weight:.2f} кг", border=1, align='C')
    pdf.cell(47.5, 8, text=f"{total_perimeter:.2f} м", border=1, align='C', new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(10)
    pdf.set_font('Roboto', 'B', 16)
    pdf.set_text_color(*pdf.primary_col)
    pdf.cell(0, 15, text=f"ЗАГАЛЬНА СУМА ДО СПЛАТИ: {grand_total:,.2f} грн", align='R', new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(5)
    pdf.set_text_color(0, 0, 0)
    pdf.set_fill_color(255, 243, 205)
    pdf.set_font('Roboto', 'B', 10)
    pdf.cell(0, 8, text=" Інформаційні повідомлення", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font('Roboto', '', 9)
    pdf.multi_cell(0, 6, text="• Продукція не може бути довжиною більше 3500 мм через проблеми з відвантаженням.\n• Біла конструкція ПВХ постачається без гарантії через менші ніж дозволено розміри (якщо застосовно).\n• Розрахунок дійсний протягом 72 годин.", border=1)

    return pdf.output()
