from fpdf import FPDF
try:
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("Roboto", style="", fname="static/Roboto-Regular.ttf")
    pdf.set_font("Roboto", size=12)
    pdf.cell(200, 10, txt="Привіт, світ!", ln=1, align='C')
    out = pdf.output()
    print("Success, output type:", type(out))
except Exception as e:
    import traceback
    traceback.print_exc()
