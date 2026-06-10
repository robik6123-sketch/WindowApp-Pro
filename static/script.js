// Глобальні налаштування додатку
const API_URL = "https://windowapp-api-72022534625.europe-west3.run.app";

// Firebase Configuration (Project: windowapp-pro-2026)
const firebaseConfig = {
    apiKey: "AIzaSy" + "DehzO9-zc3fU8oYzJJ68Qqm8Pe2UQQWlU",
    authDomain: "windowapp-pro-2026.firebaseapp.com",
    projectId: "windowapp-pro-2026",
    storageBucket: "windowapp-pro-2026.appspot.com",
    messagingSenderId: "169306359146",
    appId: "1:169306359146:web:8655828556667777"
};
firebase.initializeApp(firebaseConfig);

let idToken = null;
let userEmail = null;
window.orderCart = [];
window.currentCartOrderId = null;

// Auth State Monitor
firebase.auth().onAuthStateChanged(async (user) => {
    const userInfo = document.getElementById('user-info');
    const loginBtn = document.getElementById('btn-login');
    const historyBlock = document.querySelector('.history-card');

    if (user) {
        idToken = await user.getIdToken();
        userEmail = user.email;
        
        // Update UI
        document.getElementById('user-photo').src = user.photoURL;
        document.getElementById('user-name').textContent = user.displayName;
        userInfo.classList.remove('hidden');
        loginBtn.classList.add('hidden');
        historyBlock.classList.remove('hidden');
        
        loadOrderHistory();
    } else {
        idToken = null;
        userEmail = null;
        userInfo.classList.add('hidden');
        loginBtn.classList.remove('hidden');
        historyBlock.classList.add('hidden');
    }
});

// Login/Logout Handlers
document.getElementById('btn-login').addEventListener('click', () => {
    const provider = new firebase.auth.GoogleAuthProvider();
    firebase.auth().signInWithPopup(provider);
});

document.getElementById('btn-logout').addEventListener('click', () => {
    firebase.auth().signOut();
});

document.addEventListener('DOMContentLoaded', () => {
    // 1. UI Toggle for Arch Height
    const windowShape = document.getElementById('window-shape');
    const arcHeightGroup = document.getElementById('arc-height-group');
    windowShape.addEventListener('change', () => {
        arcHeightGroup.classList.toggle('hidden', windowShape.value !== 'arched');
    });

    // 2. Color Picker Logic
    const colorOptions = document.querySelectorAll('.color-option');
    const colorInput = document.getElementById('selected-color');
    colorOptions.forEach(opt => {
        opt.addEventListener('click', () => {
            colorOptions.forEach(o => o.classList.remove('active'));
            opt.classList.add('active');
            colorInput.value = opt.getAttribute('data-color');
        });
    });

    // 3. Dynamic Sections
    const vSectionsInput = document.getElementById('v-sections');
    const panelsContainer = document.getElementById('dynamic-panels-container');

    function renderPanelInputs() {
        const count = parseInt(vSectionsInput.value) || 1;
        panelsContainer.innerHTML = '';
        const equalProp = (100 / count).toFixed(1);
        
        for(let i=1; i<=count; i++) {
            const div = document.createElement('div');
            div.className = 'panel-config-row';
            div.innerHTML = `
                <div>
                    <label>Шир. Секції ${i} (%)</label>
                    <input type="number" class="panel-prop" data-idx="${i}" value="${equalProp}" step="0.1" min="1" max="100">
                </div>
                <div>
                    <label>Відкривання</label>
                    <select class="panel-type" data-idx="${i}">
                        <option value="fixed">Глуха</option>
                        <option value="turn_right">Поворотна (Права)</option>
                        <option value="turn_left">Поворотна (Ліва)</option>
                        <option value="tilt_turn_right">Поворот.-відкид. (Права)</option>
                        <option value="tilt_turn_left">Поворот.-відкид. (Ліва)</option>
                    </select>
                </div>
                <div style="display:flex; align-items:center; padding-top:10px;">
                    <label style="margin:0; display:flex; align-items:center; gap:5px; cursor:pointer;">
                        <input type="checkbox" class="panel-mosquito" data-idx="${i}">
                        Сітка
                    </label>
                </div>
            `;
            panelsContainer.appendChild(div);
            
            const sel = div.querySelector('.panel-type');
            const chk = div.querySelector('.panel-mosquito');
            sel.addEventListener('change', () => {
                const disable = sel.value === 'fixed';
                if(disable) chk.checked = false;
                chk.disabled = disable;
            });
            chk.disabled = true;
        }
    }
    vSectionsInput.addEventListener('change', renderPanelInputs);
    renderPanelInputs();

    // 4. Engineering Filter (Antigravity Logic)
    function validateEngineeringLimits(payload) {
        const errors = [];
        if (payload.width > 4000) errors.push("Ширина не може перевищувати 4000 мм.");
        if (payload.height > 3000) errors.push("Висота не може перевищувати 3000 мм.");
        
        if (payload.type === 'arched') {
            if (payload.arc_height < 150) errors.push("Висота арки занадто мала (мінімум 150 мм).");
            if (payload.arc_height > payload.width / 2) errors.push("Висота арки не може бути більшою за радіус (Ширина/2).");
        }
        
        if (payload.material_type === 'pvc' && payload.width > 2800) {
            errors.push("Для конструкцій ПВХ шириною > 2800 мм рекомендується використовувати алюміній для жорсткості.");
        }
        
        return errors;
    }

    // 5. Form Submit & Draw
    const form = document.getElementById('calc-form');
    let currentOrderId = null;

    document.getElementById('btn-calc').addEventListener('click', async (e) => {
        e.preventDefault();
        
        const loader = document.getElementById('loader');
        const submitBtn = document.getElementById('btn-calc');
        const errorToast = document.getElementById('error-message');
        const resultCard = document.getElementById('result-container');
        const pdfBtn = document.getElementById('btn-download-quote');

        loader.classList.remove('hidden');
        submitBtn.disabled = true;
        errorToast.classList.add('hidden');
        pdfBtn.classList.add('hidden');

        try {
            const panels = [];
            const propInputs = document.querySelectorAll('.panel-prop');
            const typeInputs = document.querySelectorAll('.panel-type');
            const mosquitoInputs = document.querySelectorAll('.panel-mosquito');
            
            for(let i=0; i<propInputs.length; i++) {
                panels.push({ 
                    proportion: parseFloat(propInputs[i].value), 
                    type: typeInputs[i].value,
                    mosquito: mosquitoInputs[i].checked
                });
            }

            const customPrices = {
                profile_price: document.getElementById('custom-prof').value,
                glass_price: document.getElementById('custom-glass').value,
                hardware_price: document.getElementById('custom-hw').value
            };

            const payload = {
                user_email: userEmail,
                width: parseFloat(document.getElementById('width').value),
                height: parseFloat(document.getElementById('height').value),
                type: document.getElementById('window-shape').value,
                arc_height: parseFloat(document.getElementById('arc-height').value),
                material_type: document.getElementById('material-type').value,
                profile: document.getElementById('profile').value,
                glass: document.getElementById('glass').value,
                color: document.getElementById('selected-color').value,
                panels: panels,
                custom_prices: customPrices,
                sill_length: parseFloat(document.getElementById('sill-length').value) || 0,
                sill_width: parseFloat(document.getElementById('sill-width').value) || 0,
                window_board: document.getElementById('window-board').value,
                window_board_length: parseFloat(document.getElementById('window-board-length').value) || 0,
                window_board_depth: parseFloat(document.getElementById('window-board-depth').value) || 0
            };

            const engErrors = validateEngineeringLimits(payload);
            if (engErrors.length > 0) {
                throw new Error("Інженерна перевірка не пройдена:\n" + engErrors.join("\n"));
            }

            // Capture Drawing Helper
            const captureView = async (viewType) => {
                drawProjections(payload, viewType);
                const svgData = new XMLSerializer().serializeToString(document.getElementById('cad-canvas'));
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                const img = new Image();
                img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgData)));
                await new Promise(r => img.onload = r);
                
                canvas.width = 1000;
                canvas.height = viewType === 'side' ? 1000 : 1000 * (payload.height / payload.width);
                if (canvas.height < 600) canvas.height = 600;

                ctx.fillStyle = "#1e1e2d";
                ctx.fillRect(0,0, canvas.width, canvas.height);
                if (viewType === 'side') {
                    // Center the side profile
                    ctx.drawImage(img, canvas.width/2 - 300, 0, 600, canvas.height);
                } else {
                    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                }
                return canvas.toDataURL('image/png');
            };

            // Generate 3 Distinct Views
            const imgInside = await captureView('inside');
            const imgOutside = await captureView('outside');
            const imgSide = await captureView('side');
            
            // Restore default view on screen
            drawProjections(payload, 'inside');
            
            // Add images to payload for PDF
            payload.images = { front: imgInside, outside: imgOutside, side: imgSide };
            
            // Show collage with correct labels
            document.getElementById('collage-section').classList.remove('hidden');
            document.getElementById('img-front').src = imgInside;
            document.getElementById('img-front').nextElementSibling.textContent = "Зсередини приміщення";
            
            document.getElementById('img-side').src = imgOutside;
            document.getElementById('img-side').nextElementSibling.textContent = "Зовні (Фасад)";
            
            document.getElementById('img-top').src = imgSide;
            document.getElementById('img-top').nextElementSibling.textContent = "Вигляд справа (Профіль)";

            const headers = { 'Content-Type': 'application/json' };
            if (idToken) headers['Authorization'] = `Bearer ${idToken}`;

            const response = await fetch(`${API_URL}/api/calculate`, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(payload)
            });

            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || data.error || 'Server Error');
            
            if (data.status === 'error') {
                throw new Error(data.message || "Помилка розрахунку на сервері");
            }
            
            if (!data.cost_details) throw new Error("Сервер повернув успішну відповідь, але дані розрахунку відсутні.");

            currentOrderId = data.order_id;
            window.lastCalculatedData = payload;
            window.lastResultData = data;
            
            // Update UI
            const cd = data.cost_details;
            document.getElementById('res-prof-cost').textContent = (cd.profile || 0).toFixed(2);
            document.getElementById('res-glass-cost').textContent = (cd.glass || 0).toFixed(2);
            document.getElementById('res-hw-cost').textContent = (cd.hardware || 0).toFixed(2);
            document.getElementById('res-extra-cost').textContent = (cd.extras || 0).toFixed(2);
            document.getElementById('res-total').innerHTML = `${(cd.total || 0).toFixed(2)} <small>грн</small>`;
            
            resultCard.classList.remove('hidden');
            // We removed the individual share and PDF buttons from here. They are in the Cart now.

            if (idToken) loadOrderHistory();

        } catch(err) {
            errorToast.textContent = err.message;
            errorToast.classList.remove('hidden');
            alert("УВАГА: " + err.message);
        } finally {
            loader.classList.add('hidden');
            submitBtn.disabled = false;
        }
    });

    // Cart Logic
    function updateCartUI() {
        const cartContainer = document.getElementById('cart-container');
        const cartList = document.getElementById('cart-items-list');
        const cartTotal = document.getElementById('cart-total');
        
        if (window.orderCart.length === 0) {
            cartContainer.classList.add('hidden');
            return;
        }
        
        cartContainer.classList.remove('hidden');
        cartList.innerHTML = '';
        let grandTotal = 0;
        
        window.orderCart.forEach((item, idx) => {
            const cost = item.result.cost_details.total;
            grandTotal += cost;
            const w = item.input.width;
            const h = item.input.height;
            const mat = item.input.material_type;
            
            cartList.innerHTML += `
                <div style="background: rgba(255,255,255,0.05); padding: 10px; border-radius: 4px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <strong>Конструкція №${idx + 1}</strong> (${w}x${h} мм, ${mat})
                    </div>
                    <div style="text-align: right;">
                        <span style="color: #0052cc; font-weight: bold;">${cost.toFixed(2)} грн</span>
                    </div>
                </div>
            `;
        });
        
        cartTotal.innerHTML = `${grandTotal.toFixed(2)} <small>грн</small>`;
        
        // Reset order state since cart changed
        window.currentCartOrderId = null;
        document.getElementById('btn-download-quote').classList.add('hidden');
        document.getElementById('share-section').classList.add('hidden');
    }

    document.getElementById('btn-add-to-cart').addEventListener('click', () => {
        if (!window.lastCalculatedData || !window.lastResultData) return;
        window.orderCart.push({
            input: window.lastCalculatedData,
            result: window.lastResultData
        });
        updateCartUI();
        // Optional: scroll to cart
        document.getElementById('cart-container').scrollIntoView({ behavior: 'smooth' });
    });

    document.getElementById('btn-create-order').addEventListener('click', async () => {
        if (window.orderCart.length === 0) return;
        const btn = document.getElementById('btn-create-order');
        btn.disabled = true;
        btn.textContent = "⌛ Формування замовлення...";
        
        try {
            const headers = { 'Content-Type': 'application/json' };
            if (idToken) headers['Authorization'] = `Bearer ${idToken}`;
            
            const payload = {
                user_email: userEmail,
                items: window.orderCart
            };
            
            const res = await fetch(`${API_URL}/api/create-order`, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            
            if (data.status === 'success') {
                window.currentCartOrderId = data.order_id;
                document.getElementById('btn-download-quote').classList.remove('hidden');
                document.getElementById('share-section').classList.remove('hidden');
                btn.textContent = "✅ Замовлення створено";
                if (idToken) loadOrderHistory();
            } else {
                throw new Error("Не вдалося створити замовлення");
            }
        } catch(e) {
            alert(e.message);
            btn.textContent = "✅ Сформувати замовлення (PDF)";
        } finally {
            btn.disabled = false;
        }
    });

    // 8. PDF Quote Download (Blob with Retry)
    document.getElementById('btn-download-quote').addEventListener('click', async () => {
        if (!window.currentCartOrderId) return;
        const btn = document.getElementById('btn-download-quote');
        const originalText = btn.textContent;
        
        const fetchQuote = async () => {
            const headers = {};
            if (idToken) headers['Authorization'] = `Bearer ${idToken}`;
            const res = await fetch(`${API_URL}/api/generate-quote/${window.currentCartOrderId}`, { headers });
            if (res.status === 404) return null;
            if (!res.ok) throw new Error("Помилка доступу до КП");
            return res.blob();
        };

        try {
            btn.textContent = "⌛ Завантаження...";
            btn.disabled = true;
            
            let blob = await fetchQuote();
            if (!blob) {
                await new Promise(r => setTimeout(r, 1500));
                blob = await fetchQuote();
            }
            if (!blob) throw new Error("Замовлення ще обробляється. Спробуйте ще раз за мить.");

            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `WindowApp_Quote_${window.currentCartOrderId}.pdf`;
            a.click();
            window.URL.revokeObjectURL(url);
            btn.textContent = "✅ Завантажено";
        } catch (e) { 
            alert(e.message); 
            btn.textContent = originalText;
        } finally {
            btn.disabled = false;
        }
    });

    // 7. Sharing Logic
    function shareReport(channel) {
        if (!window.currentCartOrderId || window.orderCart.length === 0) {
            alert("Спершу сформуйте замовлення");
            return;
        }
        
        let grandTotal = 0;
        window.orderCart.forEach(item => grandTotal += item.result.cost_details.total);
        
        const reportUrl = `${API_URL}/api/generate-quote/${window.currentCartOrderId}`;
        
        const text = `🏗 ТЕХНІЧНЕ ЗАМОВЛЕННЯ (WINDOW APP PRO)\n` +
                     `------------------------------------\n` +
                     `📦 Кількість конструкцій: ${window.orderCart.length} шт.\n` +
                     `💰 РАЗОМ: ${grandTotal.toFixed(2)} грн\n\n` +
                     `🔗 Детальна специфікація та креслення (PDF):\n${reportUrl}`;

        const encoded = encodeURIComponent(text);
        const links = {
            tg: `https://t.me/share/url?url=${encodeURIComponent(reportUrl)}&text=${encoded}`,
            vb: `viber://forward?text=${encoded}`,
            wa: `https://api.whatsapp.com/send?text=${encoded}`,
            mail: `mailto:?subject=WindowApp Order Quote&body=${encoded}`
        };
        
        // Always try to copy to clipboard as a reliable fallback for desktop
        try {
            navigator.clipboard.writeText(text);
            if (channel === 'vb') {
                // For Viber on desktop, this is often the most reliable way
                console.log("Copied to clipboard for Viber paste.");
            }
        } catch (e) {
            console.log("Clipboard write failed", e);
        }

        // Try to open the application link
        window.open(links[channel], '_blank');
        
        if (channel === 'vb') {
            const toast = document.getElementById('error-message');
            toast.textContent = "ℹ️ Текст замовлення та посилання скопійовано! Якщо Viber не відкрився автоматично, відкрийте його і натисніть Вставити (Paste).";
            toast.style.background = "#0052cc"; // Make it look like info rather than error
            toast.classList.remove('hidden');
            setTimeout(() => {
                toast.classList.add('hidden');
                toast.style.background = "rgba(255, 71, 87, 0.9)"; // reset
            }, 6000);
        }
    }

    document.getElementById('share-tg').addEventListener('click', () => shareReport('tg'));
    document.getElementById('share-vb').addEventListener('click', () => shareReport('vb'));
    document.getElementById('share-wa').addEventListener('click', () => shareReport('wa'));
    document.getElementById('share-mail').addEventListener('click', () => shareReport('mail'));

    // 7. Recent Orders History
    async function loadOrderHistory() {
        if (!idToken) return;
        const list = document.getElementById('recent-orders-list');
        try {
            const res = await fetch(`${API_URL}/api/orders`, {
                headers: { 'Authorization': `Bearer ${idToken}` }
            });
            const orders = await res.json();
            
            if (!orders || orders.length === 0) {
                list.innerHTML = '<p style="opacity:0.5;">Історія поки порожня</p>';
                return;
            }

            list.innerHTML = orders.map(o => {
                let cost = 0;
                let desc = "";
                if (o.cart && o.cart.items) {
                    o.cart.items.forEach(i => cost += i.result.cost_details.total);
                    desc = `${o.cart.items.length} конструкцій`;
                } else if (o.result) {
                    cost = o.result.cost_details.total;
                    desc = `${o.input.width}x${o.input.height} (${o.input.material_type})`;
                }
                
                return `
                <div class="order-history-item" style="padding:10px; border-bottom:1px solid rgba(0,0,0,0.05); display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <strong>#${o.id}</strong> - ${desc}
                        <br><small style="opacity:0.6;">${new Date(o.timestamp).toLocaleString()}</small>
                    </div>
                    <div style="text-align:right;">
                        <span style="color:#0052cc; font-weight:bold;">${cost.toFixed(2)} грн</span>
                        <br><button onclick="downloadQuote('${o.id}')" style="font-size:12px; color:#0052cc; border:none; background:none; cursor:pointer; padding:0;">Скачати КП</button>
                    </div>
                </div>
                `;
            }).join('');
        } catch (e) {
            list.innerHTML = '<p style="color:red;">Помилка завантаження історії</p>';
        }
    }

    window.downloadQuote = async (orderId) => {
        const headers = {};
        if (idToken) headers['Authorization'] = `Bearer ${idToken}`;
        try {
            const res = await fetch(`${API_URL}/api/generate-quote/${orderId}`, { headers });
            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `WindowApp_Quote_${orderId}.pdf`;
            a.click();
        } catch (e) { alert("Помилка завантаження"); }
    };

    // 9. Professional 2D CAD Engine (Restored with 3 Views)
    function drawProjections(payload, viewType = 'inside') {
        const svg = document.getElementById('cad-canvas');
        if (!svg) return;
        svg.innerHTML = '';
        
        const winW = payload.width;
        const winH = payload.height;
        const pad = 150;
        
        const makeSVG = (tag, attrs) => {
            const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
            for(let k in attrs) el.setAttribute(k, attrs[k]);
            return el;
        };

        // --- SIDE VIEW ---
        if (viewType === 'side') {
            svg.setAttribute('viewBox', `${-pad} ${-pad} ${winW + pad * 2} ${winH + pad * 2}`);
            const cx = winW / 2;
            const frameDepth = 70; // 70mm profile thickness
            
            // Wall Context Line
            svg.appendChild(makeSVG('line', { x1: cx, y1: -50, x2: cx, y2: winH + 100, stroke: 'rgba(255,255,255,0.1)', 'stroke-width': 2, 'stroke-dasharray': '10,10' }));
            svg.appendChild(makeSVG('text', { x: cx - 20, y: -20, fill: '#888', 'font-size': '20px', 'text-anchor': 'end' })).textContent = "ВУЛИЦЯ (Зовні)";
            svg.appendChild(makeSVG('text', { x: cx + 20, y: -20, fill: '#888', 'font-size': '20px', 'text-anchor': 'start' })).textContent = "ПРИМІЩЕННЯ (Зсередини)";

            // Window Profile
            svg.appendChild(makeSVG('rect', { x: cx - frameDepth/2, y: 0, width: frameDepth, height: winH, fill: '#ffffff', stroke: '#333', 'stroke-width': 4 }));
            
            // Glass (Double/Triple)
            svg.appendChild(makeSVG('line', { x1: cx, y1: 10, x2: cx, y2: winH - 10, stroke: '#81d4fa', 'stroke-width': 10 }));
            
            // Sill (Outside)
            if (payload.sill_width > 0) {
                const sW = payload.sill_width;
                svg.appendChild(makeSVG('path', { d: `M ${cx - frameDepth/2} ${winH} L ${cx - sW - frameDepth/2} ${winH + 20} L ${cx - sW - frameDepth/2} ${winH + 30} L ${cx - frameDepth/2} ${winH + 10} Z`, fill: '#999', stroke: '#666', 'stroke-width': 2 }));
            }
            
            // Window Board (Inside)
            if (payload.window_board !== 'none') {
                const bD = payload.window_board_depth || 200;
                svg.appendChild(makeSVG('rect', { x: cx + frameDepth/2, y: winH, width: bD, height: 30, fill: '#eee', stroke: '#999', 'stroke-width': 2 }));
            }
            return;
        }

        // --- FRONT/OUTSIDE VIEWS ---
        svg.setAttribute('viewBox', `${-pad} ${-pad} ${winW + pad * 2} ${winH + pad * 2}`);
        const frameColor = payload.color === 'anthracite' ? '#3E4349' : (payload.color === 'white' ? '#ffffff' : '#C38B40');
        
        // Group for mirroring outside view
        const mainGroup = makeSVG('g', {});
        if (viewType === 'outside') {
            mainGroup.setAttribute('transform', `translate(${winW}, 0) scale(-1, 1)`);
        }
        svg.appendChild(mainGroup);

        // 1. Draw Outer Frame Edge
        if (payload.type === 'arched') {
            const ah = payload.arc_height || 200;
            const r = (ah / 2) + (winW**2 / (8 * ah));
            const pathData = `M 0 ${winH} L 0 ${ah} A ${r} ${r} 0 0 1 ${winW} ${ah} L ${winW} ${winH} Z`;
            mainGroup.appendChild(makeSVG('path', { d: pathData, fill: frameColor, stroke: '#333', 'stroke-width': 4 }));
            
            // Glass Area Background
            const glassPath = `M 45 ${winH-45} L 45 ${ah} A ${r-45} ${r-45} 0 0 1 ${winW-45} ${ah} L ${winW-45} ${winH-45} Z`;
            mainGroup.appendChild(makeSVG('path', { d: glassPath, fill: '#e1f5fe', stroke: '#81d4fa', 'stroke-width': 1 }));
        } else {
            mainGroup.appendChild(makeSVG('rect', { x: 0, y: 0, width: winW, height: winH, fill: frameColor, stroke: '#333', 'stroke-width': 4 }));
            mainGroup.appendChild(makeSVG('rect', { x: 45, y: 45, width: winW - 90, height: winH - 90, fill: '#e1f5fe', stroke: '#81d4fa', 'stroke-width': 1 }));
        }

        // 3. Draw Panels
        let currentX = 0;
        payload.panels.forEach((p, i) => {
            const pW = winW * (p.proportion / 100);
            
            // Panel Inner Frame
            mainGroup.appendChild(makeSVG('rect', { 
                x: currentX + 5, y: 5, width: pW - 10, height: winH - 10, 
                fill: 'none', stroke: frameColor, 'stroke-width': 40 
            }));

            // Glass/Filling
            mainGroup.appendChild(makeSVG('rect', { 
                x: currentX + 25, y: 25, width: pW - 50, height: winH - 50, 
                fill: '#b3e5fc', stroke: '#4fc3f7', 'stroke-width': 1 
            }));

            // Dimensions (Only on inside view to avoid mirrored text)
            if (viewType === 'inside') {
                svg.appendChild(makeSVG('text', { 
                    x: currentX + pW/2, y: winH/2, 
                    fill: '#e65100', 'font-size': '42px', 'font-weight': 'bold', 'text-anchor': 'middle'
                })).textContent = `${Math.round(pW - 100)} x ${Math.round(winH - 100)}`;
            }

            // Opening Arrows
            if (p.type !== 'fixed') {
                const isRight = p.type.includes('right');
                const isTilt = p.type.includes('tilt');
                const xStart = isRight ? currentX + pW - 55 : currentX + 55;
                const xEnd = isRight ? currentX + 55 : currentX + pW - 55;
                
                // Solid line for 'pull' (inside), dashed for 'push' (outside)
                const arrowStyle = viewType === 'inside' ? 'none' : '20,10';
                mainGroup.appendChild(makeSVG('path', { 
                    d: `M ${xStart} 80 L ${xEnd} ${winH/2} L ${xStart} ${winH - 80}`, 
                    fill: 'none', stroke: '#333', 'stroke-width': 5, 'stroke-dasharray': arrowStyle 
                }));
                
                if (isTilt) {
                    mainGroup.appendChild(makeSVG('path', { 
                        d: `M ${currentX + 80} ${winH - 80} L ${currentX + pW/2} 80 L ${currentX + pW - 80} ${winH - 80}`, 
                        fill: 'none', stroke: '#555', 'stroke-width': 5, 'stroke-dasharray': arrowStyle 
                    }));
                }
            }

            // Mosquito Net (Only visible from outside view)
            if (p.mosquito && viewType === 'outside') {
                const netPattern = makeSVG('pattern', { id: `net-${i}`, x: 0, y: 0, width: 8, height: 8, patternUnits: 'userSpaceOnUse' });
                netPattern.appendChild(makeSVG('path', { d: 'M 8 0 L 0 0 0 8', fill: 'none', stroke: 'rgba(0,0,0,0.5)', 'stroke-width': 1 }));
                svg.appendChild(netPattern); // pattern definition outside mainGroup is fine
                mainGroup.appendChild(makeSVG('rect', { 
                    x: currentX + 30, y: 30, width: pW - 60, height: winH - 60, 
                    fill: `url(#net-${i})`, opacity: 0.8 
                }));
                // We don't draw text "СІТКА" on outside view to avoid mirrored text, the grid is enough.
            }

            currentX += pW;
        });

        // 4. Outer Labels & Sills (Only on Inside view for clarity, or not mirrored)
        if (viewType === 'inside') {
            const dimStyle = { fill: '#fff', 'font-size': '36px', 'text-anchor': 'middle' };
            svg.appendChild(makeSVG('text', { x: winW/2, y: winH + 80, ...dimStyle })).textContent = `${winW} mm`;
            const vDim = makeSVG('text', { x: -80, y: winH/2, ...dimStyle, transform: `rotate(-90, -80, ${winH/2})` });
            vDim.textContent = `${winH} mm`;
            svg.appendChild(vDim);

            if (payload.window_board !== 'none') {
                svg.appendChild(makeSVG('rect', { x: -20, y: winH + 10, width: winW + 40, height: 40, fill: '#eee', stroke: '#999', rx: 4 }));
                svg.appendChild(makeSVG('text', { x: winW/2, y: winH + 38, fill: '#333', 'font-size': '24px', 'text-anchor': 'middle' })).textContent = `Підвіконня: ${payload.window_board_length || winW}x${payload.window_board_depth || 200}`;
            }
            if (payload.sill_width > 0) {
                svg.appendChild(makeSVG('rect', { x: -10, y: winH + 60, width: winW + 20, height: 30, fill: '#999', stroke: '#666', rx: 4 }));
                svg.appendChild(makeSVG('text', { x: winW/2, y: winH + 82, fill: '#fff', 'font-size': '22px', 'text-anchor': 'middle' })).textContent = `Відлив: ${payload.sill_length || winW}x${payload.sill_width}`;
            }
        }

    }
});
