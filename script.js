const DB = {
    profiles: { "WDS_5": 950, "REHAU_70": 1450, "SYNEGO": 2200 },
    fillings: { "glass_24": 1100, "glass_40": 1750, "sandwich": 650 },
    hardware: { "fixed": 0, "turn": 1000, "tilt-turn": 1600 }
};

function updateSectionControls() {
    const vSec = parseInt(document.getElementById('v-sections').value) || 1;
    const container = document.getElementById('sections-controls');
    if (container.children.length !== vSec) {
        container.innerHTML = "";
        for (let i = 1; i <= vSec; i++) {
            container.innerHTML += `
                <div style="display:flex; gap:5px; margin-bottom:5px;">
                    <input type="number" id="ratio-${i}" value="100" style="width:50px;" oninput="calculate()">
                    <select id="sash-${i}" onchange="calculate()" style="flex-grow:1; font-size:11px;">
                        <option value="fixed">Глуха</option>
                        <option value="turn-l">Поворотна (L)</option>
                        <option value="turn-r">Поворотна (R)</option>
                        <option value="tilt-turn-l">Повор-відкидна (L)</option>
                        <option value="tilt-turn-r">Повор-відкидна (R)</option>
                    </select>
                </div>`;
        }
    }
}

function calculate() {
    updateSectionControls();
    const w = parseFloat(document.getElementById('width').value) || 0;
    const h = parseFloat(document.getElementById('height').value) || 0;
    const vSec = parseInt(document.getElementById('v-sections').value) || 1;
    const hSec = parseInt(document.getElementById('h-sections').value) || 1;
    const color = document.getElementById('color-type').value;
    const profKey = document.getElementById('profile-type').value;
    const fillKey = document.getElementById('filling-type').value;

    const manualProf = parseFloat(document.getElementById('price-prof-manual').value) || DB.profiles[profKey];
    const manualFill = parseFloat(document.getElementById('price-fill-manual').value) || DB.fillings[fillKey];

    const drawBox = document.getElementById('window-draw');
    const container = document.querySelector('.canvas-container');
    const scale = Math.min((container.clientWidth - 200) / w, (container.clientHeight - 200) / h, 0.45);

    const drawW = w * scale;
    const drawH = h * scale;

    drawBox.style.width = drawW + 'px';
    drawBox.style.height = drawH + 'px';
    drawBox.style.borderColor = color;
    
    // Створюємо SVG шар для ліній
    let svgOverlay = `<svg width="${drawW}" height="${drawH}" style="position:absolute; top:0; left:0; z-index:6; overflow:visible;">`;

    drawBox.innerHTML = `<span id="label-height">${h} мм</span><span id="label-width">${w} мм</span>`;

    let totalRatio = 0;
    for(let i=1; i<=vSec; i++) totalRatio += (parseFloat(document.getElementById(`ratio-${i}`).value) || 100);

    let currentX_mm = 0;
    let totalHardPrice = 0;

    for(let i = 0; i < vSec; i++) {
        let ratioVal = parseFloat(document.getElementById(`ratio-${i+1}`).value) || 100;
        let sW_mm = (w * (ratioVal / totalRatio));
        let sW_px = sW_mm * scale;
        let curX_px = currentX_mm * scale;
        const sType = document.getElementById(`sash-${i+1}`).value;

        if (sType !== 'fixed') {
            totalHardPrice += sType.includes('tilt') ? DB.hardware["tilt-turn"] : DB.hardware["turn"];
        }

        if (i > 0) drawBox.innerHTML += `<div class="impost-v" style="left: ${curX_px}px; background: ${color}"></div>`;

        for(let j = 0; j < hSec; j++) {
            let sH_mm = h / hSec;
            let sH_px = sH_mm * scale;
            let curY_px = (sH_mm * j) * scale;
            
            if (j > 0) drawBox.innerHTML += `<div class="impost-h" style="top: ${curY_px}px; background: ${color}"></div>`;
            
            const label = (fillKey === "sandwich") ? "СЕНДВІЧ" : `${Math.round(sW_mm - 80)}x${Math.round(sH_mm - 80)}`;
            drawBox.innerHTML += `<div class="glass-label" style="left:${curX_px + sW_px/2}px; top:${curY_px + sH_px/2}px">${label}</div>`;

            if (sType !== 'fixed' && j === 0) {
                const isL = sType.includes('-l');
                const xStart = isL ? curX_px + sW_px : curX_px;
                const xEnd = isL ? curX_px : curX_px + sW_px;
                
                svgOverlay += `<line x1="${xStart}" y1="${curY_px}" x2="${xEnd}" y2="${curY_px + sH_px/2}" stroke="red" stroke-width="2" />`;
                svgOverlay += `<line x1="${xStart}" y1="${curY_px + sH_px}" x2="${xEnd}" y2="${curY_px + sH_px/2}" stroke="red" stroke-width="2" />`;
                if(sType.includes('tilt')) {
                    svgOverlay += `<line x1="${curX_px}" y1="${curY_px}" x2="${curX_px + sW_px}" y2="${curY_px}" stroke="red" stroke-width="2" stroke-dasharray="5" />`;
                }
            }
        }
        currentX_mm += sW_mm;
    }

    svgOverlay += `</svg>`;
    drawBox.innerHTML += svgOverlay;

    const area = (w * h) / 1000000;
    const total = (area * manualProf) + (area * manualFill) + totalHardPrice + 800;
    
    const resHtml = `
        <strong>${profKey}</strong> | ${area.toFixed(2)} м²<br>
        Ціна м²: ${(manualProf + manualFill).toFixed(0)} грн | Фурнітура: ${totalHardPrice} грн<br>
        <hr><strong>РАЗОМ: ${total.toFixed(0)} грн</strong>
    `;
    document.getElementById('result').innerHTML = resHtml;

    // Готуємо дані для PDF
    document.getElementById('pdf-data-table').innerHTML = `
        <table style="width:100%; border-collapse:collapse; margin-top:20px; font-family:sans-serif;">
            <tr style="background:#f1f3f5;"><th style="border:1px solid #ddd; padding:8px;">Параметр</th><th style="border:1px solid #ddd; padding:8px;">Значення</th></tr>
            <tr><td style="border:1px solid #ddd; padding:8px;">Профіль</td><td style="border:1px solid #ddd; padding:8px;">${profKey}</td></tr>
            <tr><td style="border:1px solid #ddd; padding:8px;">Заповнення</td><td style="border:1px solid #ddd; padding:8px;">${fillKey}</td></tr>
            <tr><td style="border:1px solid #ddd; padding:8px;">Розміри</td><td style="border:1px solid #ddd; padding:8px;">${w} x ${h} мм</td></tr>
            <tr><td style="border:1px solid #ddd; padding:8px;">Площа</td><td style="border:1px solid #ddd; padding:8px;">${area.toFixed(2)} м²</td></tr>
            <tr style="font-weight:bold; color:#1a73e8;"><td style="border:1px solid #ddd; padding:8px;">ЗАГАЛЬНА ВАРТІСТЬ</td><td style="border:1px solid #ddd; padding:8px;">${total.toFixed(0)} грн</td></tr>
        </table>
    `;
}

function generatePDF() {
    calculate();
    const exportArea = document.getElementById('export-container');
    const windowView = document.getElementById('window-draw').cloneNode(true);
    
    const pdfView = document.getElementById('pdf-window-view');
    pdfView.innerHTML = "";
    pdfView.appendChild(windowView);
    
    document.getElementById('pdf-order-id').innerText = Math.floor(Math.random()*10000);

    const opt = {
        margin: 10,
        filename: 'Window_Order_Pro.pdf',
        image: { type: 'jpeg', quality: 1 },
        html2canvas: { scale: 2, useCORS: true },
        jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
    };
    html2pdf().set(opt).from(exportArea).save();
}

window.onload = calculate;
