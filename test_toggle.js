const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  await page.goto('http://127.0.0.1:5000/');
  
  // Wait for initial calc
  await page.waitForTimeout(1000);
  
  // Click the 3D button
  await page.evaluate(() => {
    const btn = document.getElementById('btn-3d');
    if (btn) btn.click();
  });
  
  await page.waitForTimeout(1000);
  
  // Check the active state of buttons and if 3D scene is visible
  const state = await page.evaluate(() => {
    return {
      btn3dActive: document.getElementById('btn-3d').classList.contains('active'),
      btn2dActive: document.getElementById('btn-2d').classList.contains('active'),
      canvasThreeHidden: document.getElementById('three-container').classList.contains('hidden'),
      canvasSvgHidden: document.getElementById('cad-canvas').classList.contains('hidden')
    };
  });
  
  console.log("STATE AFTER CLICK:", state);
  
  // Check for any alerts triggered
  page.on('dialog', async dialog => {
    console.log("ALERT PRESENT:", dialog.message());
    await dialog.dismiss();
  });
  
  await browser.close();
})();
