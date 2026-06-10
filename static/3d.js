// static/3d.js

let scene, camera, renderer, controls;
let windowGroup = null;

function init3DScene() {
    const container = document.getElementById('three-container');
    
    scene = new THREE.Scene();
    scene.background = null; // transparent to show orb background

    camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 1, 15000);
    // position will be updated dynamically based on window size
    
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(window.devicePixelRatio);
    container.appendChild(renderer.domElement);

    const hemiLight = new THREE.HemisphereLight(0xffffff, 0x444444, 0.8);
    hemiLight.position.set(0, 2000, 0);
    scene.add(hemiLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(1000, 2000, 1000);
    scene.add(dirLight);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;

    window.addEventListener('resize', onWindowResize);
    
    animate();
}

function onWindowResize() {
    const container = document.getElementById('three-container');
    if (!container || container.clientWidth === 0) return;
    camera.aspect = container.clientWidth / container.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(container.clientWidth, container.clientHeight);
    console.log("3D Resized to", container.clientWidth, "x", container.clientHeight);
}

function animate() {
    requestAnimationFrame(animate);
    if (controls) controls.update();
    if (renderer && scene && camera) renderer.render(scene, camera);
}

window.update3DModel = function(payload) {
    if (!scene) init3DScene();

    if (windowGroup) {
        scene.remove(windowGroup);
        windowGroup = null;
    }

    windowGroup = new THREE.Group();
    
    const winW = payload.width;
    const winH = payload.height;
    const isArched = payload.type === 'arched';
    const ah = payload.arc_height || 400;
    
    const maxDim = Math.max(winW, winH);
    camera.position.set(winW / 2, winH / 2, maxDim * 1.8);
    controls.target.set(winW / 2, winH / 2, 0);
    controls.update();

    let frameColorHex = 0xffffff;
    if (payload.color === 'anthracite') frameColorHex = 0x3E4349;
    if (payload.color === 'golden_oak') frameColorHex = 0xC38B40;
    if (payload.color === 'silver') frameColorHex = 0xC0C0C0;

    const isAluminum = payload.material_type === 'aluminum';
    const frameMat = new THREE.MeshStandardMaterial({ 
        color: frameColorHex, 
        roughness: isAluminum ? 0.3 : 0.5, 
        metalness: isAluminum ? 0.6 : 0.1 
    });

    const glassMat = new THREE.MeshPhysicalMaterial({
        color: 0xaaccff,
        metalness: 0.2,
        roughness: 0.1,
        transmission: 0.8,
        transparent: true
    });

    const frameThickness = isAluminum ? 45 : 60; 
    const frameDepth = isAluminum ? 70 : 70;

    function createBox(w, h, d, mat, x, y, z) {
        const geo = new THREE.BoxGeometry(w, h, d);
        geo.translate(w/2, h/2, d/2);
        const mesh = new THREE.Mesh(geo, mat);
        mesh.position.set(x, y, z);
        windowGroup.add(mesh);
    }

    if (isArched) {
        // Bottom and Sides
        const rh = winH - ah;
        createBox(winW, frameThickness, frameDepth, frameMat, 0, 0, -frameDepth/2); // Bottom
        createBox(frameThickness, rh - frameThickness, frameDepth, frameMat, 0, frameThickness, -frameDepth/2); // Left
        createBox(frameThickness, rh - frameThickness, frameDepth, frameMat, winW - frameThickness, frameThickness, -frameDepth/2); // Right
        
        // Arch Geometry
        const r = (ah / 2) + (winW**2 / (8 * ah));
        const shape = new THREE.Shape();
        // Outer arc
        shape.moveTo(0, rh);
        shape.absarc(winW/2, rh - (r - ah), r, Math.PI - Math.asin(winW/(2*r)), Math.asin(winW/(2*r)), false);
        // Inner arc hole
        const innerR = r - frameThickness;
        const hole = new THREE.Path();
        hole.moveTo(frameThickness, rh);
        hole.absarc(winW/2, rh - (r - ah), innerR, Math.PI - Math.asin((winW-2*frameThickness)/(2*innerR)), Math.asin((winW-2*frameThickness)/(2*innerR)), false);
        shape.holes.push(hole);

        const extrudeSettings = { depth: frameDepth, bevelEnabled: false };
        const arcGeo = new THREE.ExtrudeGeometry(shape, extrudeSettings);
        const arcMesh = new THREE.Mesh(arcGeo, frameMat);
        arcMesh.position.z = -frameDepth;
        windowGroup.add(arcMesh);

        // Glass Arched
        const glassShape = new THREE.Shape();
        glassShape.moveTo(frameThickness, frameThickness);
        glassShape.lineTo(winW - frameThickness, frameThickness);
        glassShape.lineTo(winW - frameThickness, rh);
        glassShape.absarc(winW/2, rh - (r - ah), innerR, Math.asin((winW-2*frameThickness)/(2*innerR)), Math.PI - Math.asin((winW-2*frameThickness)/(2*innerR)), true);
        glassShape.lineTo(frameThickness, rh);
        
        const glassGeo = new THREE.ExtrudeGeometry(glassShape, { depth: 24, bevelEnabled: false });
        const glassMesh = new THREE.Mesh(glassGeo, glassMat);
        glassMesh.position.z = -12;
        windowGroup.add(glassMesh);

    } else {
        // Regular Rectangular
        createBox(winW, frameThickness, frameDepth, frameMat, 0, winH - frameThickness, -frameDepth/2);
        createBox(winW, frameThickness, frameDepth, frameMat, 0, 0, -frameDepth/2);
        createBox(frameThickness, winH - 2*frameThickness, frameDepth, frameMat, 0, frameThickness, -frameDepth/2);
        createBox(frameThickness, winH - 2*frameThickness, frameDepth, frameMat, winW - frameThickness, frameThickness, -frameDepth/2);
        
        createBox(winW - 2*frameThickness, winH - 2*frameThickness, 24, glassMat, frameThickness, frameThickness, -12);
    }

    scene.add(windowGroup);
};

// --- New: Collage Generation Logic ---
window.takeSnapshots = function() {
    if (!renderer || !scene || !windowGroup) {
        console.error("3D Scene or Model not ready for snapshots");
        return null;
    }

    const container = document.getElementById('three-container');
    const originalVisible = container.classList.contains('hidden');

    // Force show and resize for capture
    if (originalVisible) container.classList.remove('hidden');
    
    // Ensure renderer matches container size even if hidden before
    const width = container.clientWidth || 800;
    const height = container.clientHeight || 600;
    renderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();

    const snapshots = {};
    const payload = window.lastCalculatedData;
    const winW = payload?.width || 1000;
    const winH = payload?.height || 1000;
    const maxDim = Math.max(winW, winH);

    // Helper to capture with forced render
    const capture = (name, x, y, z) => {
        camera.position.set(x, y, z);
        camera.lookAt(winW/2, winH/2, 0);
        renderer.render(scene, camera);
        // Sometimes one render is not enough for the buffer to fill
        renderer.render(scene, camera); 
        snapshots[name] = renderer.domElement.toDataURL('image/png');
        console.log(`Snapshot [${name}] captured. Length: ${snapshots[name].length}`);
    };

    try {
        // 1. Front View
        capture('front', winW/2, winH/2, maxDim * 2.2);
        
        // 2. Side View
        capture('side', winW + maxDim * 1.2, winH/2, 0);
        
        // 3. Isometric View
        capture('iso', winW * 1.5, winH * 1.5, maxDim * 1.5);
    } catch (e) {
        console.error("Snapshot failed:", e);
    }

    // Restore hidden state
    if (originalVisible) container.classList.add('hidden');
    
    return snapshots;
};
