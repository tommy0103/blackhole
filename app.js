import * as THREE from 'https://unpkg.com/three@0.169.0/build/three.module.js';

// ============================================================================
// 1. WebGL Relativistic Black Hole Centered Inside the 3-Column Stage
// ============================================================================
const stageWrap = document.getElementById('stage-canvas-wrap');
const webglCanvas = document.getElementById('webgl-canvas');
const flowCanvas = document.getElementById('flow-canvas');
const ctx = flowCanvas.getContext('2d');

const renderer = new THREE.WebGLRenderer({
  canvas: webglCanvas,
  antialias: true,
  alpha: true,
  powerPreference: 'high-performance',
});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

const scene = new THREE.Scene();
const orthoCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

const uniforms = {
  uTime: { value: 0 },
  uResolution: { value: new THREE.Vector2(800, 500) },
  uAirgap: { value: 0 },
  uFlash: { value: 0 },
};

const vertexShader = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position, 1.0);
  }
`;

const fragmentShader = /* glsl */ `
  precision highp float;
  varying vec2 vUv;
  uniform float uTime;
  uniform vec2 uResolution;
  uniform float uAirgap;
  uniform float uFlash;

  float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
  }

  float noise2(vec2 x) {
    vec2 i = floor(x);
    vec2 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    float a = hash12(i);
    float b = hash12(i + vec2(1.0, 0.0));
    float c = hash12(i + vec2(0.0, 1.0));
    float d = hash12(i + vec2(1.0, 1.0));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
  }

  float fbm2(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    mat2 m = mat2(0.8, 0.6, -0.6, 0.8);
    for (int i = 0; i < 4; i++) {
      v += a * noise2(p);
      p = m * p * 2.03 + vec2(11.3, 7.1);
      a *= 0.5;
    }
    return v;
  }

  vec3 blackbodyColor(float temp, float airgap) {
    vec3 c0 = mix(vec3(0.08, 0.01, 0.002), vec3(0.10, 0.002, 0.01), airgap);
    vec3 c1 = mix(vec3(0.85, 0.16, 0.01),  vec3(0.92, 0.03, 0.08),  airgap);
    vec3 c2 = mix(vec3(1.00, 0.58, 0.12),  vec3(1.00, 0.22, 0.18),  airgap);
    vec3 c3 = mix(vec3(1.00, 0.93, 0.76),  vec3(1.00, 0.78, 0.78),  airgap);
    vec3 c4 = mix(vec3(0.90, 0.96, 1.00),  vec3(1.00, 0.90, 0.92),  airgap);

    vec3 col = mix(c0, c1, smoothstep(0.0, 0.32, temp));
    col = mix(col, c2, smoothstep(0.28, 0.65, temp));
    col = mix(col, c3, smoothstep(0.60, 0.95, temp));
    col = mix(col, c4, smoothstep(0.95, 1.45, temp));
    return col;
  }

  vec4 sampleAccretionDisk(vec3 hitP, vec3 camPos, float rs, int crossingOrder) {
    float r = length(hitP.xz);
    float innerR = 1.42 * rs;
    float outerR = 5.20 * rs;

    if (r < innerR || r > outerR) return vec4(0.0);

    float u = (r - innerR) / (outerR - innerR);
    float radialEnv = smoothstep(0.0, 0.05, u) * pow(1.0 - smoothstep(0.15, 1.0, u), 1.65);

    float phi = atan(hitP.z, hitP.x);
    float omega = 2.8 / pow(max(r / rs, 1.1), 1.35);
    float rotPhi = phi + uTime * omega;

    float n1 = fbm2(vec2(r * 4.5 - uTime * 0.18, sin(rotPhi) * 2.2 + cos(rotPhi) * 2.2));
    float n2 = fbm2(vec2(r * 11.5, rotPhi * 3.0 + n1 * 3.2 - uTime * 0.4));
    float rings = 0.5 + 0.5 * sin(r * 28.0 / rs + n1 * 7.0);
    float fineRings = 0.5 + 0.5 * sin(r * 68.0 / rs + n2 * 6.0);

    float filament = mix(rings, fineRings, 0.45) * (0.45 + 0.85 * n2);

    vec3 tangent = normalize(vec3(-hitP.z, 0.0, hitP.x));
    vec3 viewToHit = normalize(hitP - camPos);
    float cosTheta = dot(tangent, -viewToHit);

    float beta = clamp(0.55 * sqrt(rs / max(r, innerR)), 0.1, 0.52);
    float gamma = inversesqrt(1.0 - beta * beta);
    float dopplerFactor = 1.0 / (gamma * (1.0 - beta * cosTheta));
    float beaming = pow(clamp(dopplerFactor, 0.45, 1.72), 2.75);

    float baseTemp = pow(1.0 - u, 0.72) * (0.55 + 0.75 * filament);
    float temp = baseTemp * mix(1.0, beaming, 0.62);

    vec3 col = blackbodyColor(temp, uAirgap);
    float orderBoost = crossingOrder == 0 ? 1.0 : 1.4;
    vec3 emission = col * radialEnv * (0.35 + 1.45 * filament) * beaming * 2.4 * orderBoost;
    float alpha = clamp(radialEnv * (0.45 + 0.65 * filament), 0.0, 0.96);

    return vec4(emission, alpha);
  }

  void main() {
    vec2 uv = (vUv - 0.5) * 2.0;
    uv.x *= uResolution.x / uResolution.y;

    // Compact scale so the black hole fits cleanly in the center column between Left & Right UI
    float camDist = 11.8;
    float pitch = 0.19;
    float roll = -0.14;

    vec3 ro = vec3(0.0, sin(pitch) * camDist, cos(pitch) * camDist);
    vec3 ta = vec3(0.0, 0.0, 0.0);
    vec3 ww = normalize(ta - ro);
    vec3 up = vec3(sin(roll), cos(roll), 0.0);
    vec3 uu = normalize(cross(ww, up));
    vec3 vv = normalize(cross(uu, ww));

    float fov = 1.55;
    vec3 rd = normalize(uv.x * uu + uv.y * vv + fov * ww);

    float rs = 1.0;
    vec3 p = ro;
    vec3 v = rd;
    vec3 Lvec = cross(p, v);
    float L2 = dot(Lvec, Lvec);

    vec3 accumColor = vec3(0.0);
    float transmittance = 1.0;
    float minR = 1000.0;
    int crossings = 0;

    for (int i = 0; i < 128; i++) {
      float r = length(p);
      minR = min(minR, r);

      if (r <= rs * 1.005) {
        break;
      }
      if (r > 16.0 && i > 24) {
        break;
      }

      float h = clamp(0.045 * (r - rs * 0.82), 0.014, 0.26);

      vec3 a1 = -1.5 * rs * L2 * p / pow(r, 5.0);
      vec3 vMid = normalize(v + 0.5 * h * a1);
      vec3 pMid = p + 0.5 * h * vMid;
      float rMid = max(length(pMid), rs * 1.001);
      vec3 a2 = -1.5 * rs * L2 * pMid / pow(rMid, 5.0);

      vec3 nextV = normalize(v + h * a2);
      vec3 nextP = p + h * vMid;

      if (p.y * nextP.y <= 0.0 && crossings < 3) {
        float tCross = abs(p.y) / max(abs(p.y - nextP.y), 1e-5);
        vec3 hitP = mix(p, nextP, tCross);
        vec4 diskSample = sampleAccretionDisk(hitP, ro, rs, crossings);
        if (diskSample.a > 0.001) {
          accumColor += transmittance * diskSample.rgb;
          transmittance *= (1.0 - diskSample.a * 0.88);
        }
        crossings++;
      }

      p = nextP;
      v = nextV;
      if (transmittance < 0.015) break;
    }

    float photonSphereDist = abs(minR - 1.48 * rs) / rs;
    float tightPhotonRing = exp(-pow(photonSphereDist / 0.022, 2.0)) * (2.2 + uFlash * 2.5);
    float softCorona = exp(-pow(max(0.0, minR - 1.32 * rs) / (0.45 * rs), 1.5)) * (0.28 + uFlash * 0.3);
    vec3 photonCol = blackbodyColor(1.18, uAirgap);

    vec3 col = accumColor + (tightPhotonRing + softCorona) * photonCol;

    vec3 x = max(vec3(0.0), col * 0.9);
    col = (x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14);
    col = pow(clamp(col, 0.0, 1.0), vec3(0.92));

    gl_FragColor = vec4(col, 1.0);
  }
`;

const quad = new THREE.Mesh(
  new THREE.PlaneGeometry(2, 2),
  new THREE.ShaderMaterial({
    vertexShader,
    fragmentShader,
    uniforms,
    depthWrite: false,
    depthTest: false,
  })
);
scene.add(quad);

function resizeStage() {
  const rect = stageWrap.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio, 2);
  renderer.setSize(rect.width, rect.height);
  uniforms.uResolution.value.set(rect.width, rect.height);

  flowCanvas.width = rect.width * dpr;
  flowCanvas.height = rect.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener('resize', resizeStage);
resizeStage();

// ============================================================================
// 2. 2D Particle Flow Engine (Left Host -> Black Hole Filter -> Right Container)
// ============================================================================
let hoveredStreamIndex = null;
let targetAirgap = 0;
let currentLang = 'en';

const streamItems = Array.from(document.querySelectorAll('.stream-item'));
const outIdentity = document.getElementById('out-card-identity');
const outWorkspace = document.getElementById('out-card-workspace');
const outClipboard = document.getElementById('out-card-clipboard');
const captionEl = document.getElementById('singularity-caption');

const streamDescriptions = {
  en: [
    '<strong>Hardware UUID (ioreg)</strong>: Swallowed by the container boundary — Claude sees generic Debian Linux, never your Mac serial.',
    '<strong>Region & Locale (zh_CN)</strong>: Stripped by strict env allowlist — zero host locale or timezone variables enter the VM.',
    '<strong>~/.ssh & Cloud Keys</strong>: Unmounted — host $HOME does not exist inside the container.',
    '<strong>Other Repos (~/Code/*)</strong>: Unmounted — only the single folder where you ran claude is exposed.',
    '<strong>Direct ISP / IPv6 IP</strong>: Blocked via sysctl IPv6 disable + fail-close utun watchdog.',
    '<strong>Current Dir ($PWD)</strong>: Slingshots across the horizon as <strong>/workspace</strong> over virtiofs (3.2 GB/s live sync).',
    '<strong>Pasted Image ($TMPDIR)</strong>: Mirrored read-only into the container at the exact same path in 0.5s.',
  ],
  zh: [
    '<strong>硬件 UUID (ioreg)</strong>：被黑洞彻底吞噬 —— 容器内只有纯净 Debian Linux，读取不到任何 Mac 硬件序列号。',
    '<strong>系统地区与语言 (zh_CN)</strong>：被环境变量白名单剥离 —— 宿主机地区、语言、时区变量全部归零。',
    '<strong>~/.ssh 与云厂商密钥</strong>：物理隔离不挂载 —— 容器内根本不存在宿主机的 $HOME 目录。',
    '<strong>其他代码仓库 (~/Code/*)</strong>：物理隔离不挂载 —— 仅暴露当前执行 claude 的单一目录。',
    '<strong>真实宽带 IP / IPv6</strong>：内核级禁用 IPv6 + 故障闭锁 utun 路由守卫，严防直连泄漏。',
    '<strong>当前项目目录 ($PWD)</strong>：引力弹弓穿透视界，映射为容器内 <strong>/workspace</strong>（virtiofs 3.2 GB/s 实时双向同步）。',
    '<strong>终端粘贴截图 ($TMPDIR)</strong>：0.5 秒自动镜像并以同名路径只读挂载进容器，贴图直接可用。',
  ],
};

streamItems.forEach((el, idx) => {
  el.addEventListener('pointerenter', () => {
    hoveredStreamIndex = idx;
    streamItems.forEach(s => s.classList.remove('active'));
    el.classList.add('active');
    captionEl.innerHTML = streamDescriptions[currentLang][idx];
    uniforms.uFlash.value = 0.5;
  });
  el.addEventListener('pointerleave', () => {
    hoveredStreamIndex = null;
    el.classList.remove('active');
    captionEl.innerHTML = currentLang === 'zh'
      ? '悬停左侧任意数据流 · <strong>5 项宿主机特征被吞噬</strong> · <strong>2 项工作流穿透至容器</strong>'
      : 'Hover any stream on the left · <strong>5 swallowed</strong> into horizon · <strong>2 lensed</strong> into VM';
  });
});

// Spawn continuous particles along the 7 streams
const particles = [];
for (let i = 0; i < 95; i++) {
  const streamIdx = i % 7;
  particles.push({
    streamIdx,
    t: Math.random(),
    speed: (streamIdx >= 5 ? 0.36 : 0.28) + Math.random() * 0.12,
    size: streamIdx >= 5 ? 2.6 : 2.1,
    spiralOffset: (Math.random() - 0.5) * 0.35,
  });
}

function cubicBezier(p0, p1, p2, p3, t) {
  const u = 1 - t;
  const tt = t * t, uu = u * u;
  const uuu = uu * u, ttt = tt * t;
  return {
    x: uuu * p0.x + 3 * uu * t * p1.x + 3 * u * tt * p2.x + ttt * p3.x,
    y: uuu * p0.y + 3 * uu * t * p1.y + 3 * u * tt * p2.y + ttt * p3.y,
  };
}

function drawStreams(dt, now) {
  const wrapRect = stageWrap.getBoundingClientRect();
  ctx.clearRect(0, 0, wrapRect.width, wrapRect.height);

  // Skip on narrow mobile stacked layout
  if (window.innerWidth <= 1024) return;

  const center = {
    x: wrapRect.width * 0.5,
    y: wrapRect.height * 0.5,
  };

  // Compute start points from the 7 left items
  const starts = streamItems.map(el => {
    const r = el.getBoundingClientRect();
    return {
      x: r.right - wrapRect.left,
      y: r.top + r.height * 0.5 - wrapRect.top,
    };
  });

  // Compute target points on the right column for the 2 passed streams
  const wsRect = outWorkspace.getBoundingClientRect();
  const cbRect = outClipboard.getBoundingClientRect();
  const targetWorkspace = {
    x: wsRect.left - wrapRect.left,
    y: wsRect.top + wsRect.height * 0.5 - wrapRect.top,
  };
  const targetClipboard = {
    x: cbRect.left - wrapRect.left,
    y: cbRect.top + cbRect.height * 0.5 - wrapRect.top,
  };

  // 1. Draw guide curves for each of the 7 streams
  starts.forEach((start, idx) => {
    const isHighlighted = hoveredStreamIndex === null || hoveredStreamIndex === idx;
    const baseAlpha = hoveredStreamIndex === null ? 0.26 : (isHighlighted ? 0.72 : 0.06);

    ctx.beginPath();
    ctx.moveTo(start.x, start.y);

    if (idx < 5) {
      // RED SWALLOWED STREAM: curves into the Black Hole's event horizon and terminates inside the shadow
      const cp1 = { x: start.x + (center.x - start.x) * 0.52, y: start.y };
      const cp2 = { x: center.x - 42, y: center.y + (idx - 2) * 18 };
      ctx.bezierCurveTo(cp1.x, cp1.y, cp2.x, cp2.y, center.x, center.y);
      ctx.strokeStyle = `rgba(244, 63, 94, ${baseAlpha})`;
      ctx.lineWidth = hoveredStreamIndex === idx ? 2.0 : 1.1;
      ctx.stroke();
    } else {
      // GOLD SLINGSHOT STREAM: bends around the lower photon ring and shoots into the Right Column!
      const end = idx === 5 ? targetWorkspace : targetClipboard;
      const lensApexY = center.y + (idx === 5 ? 64 : 84);
      const cp1 = { x: center.x - 70, y: lensApexY };
      const cp2 = { x: center.x + 70, y: lensApexY };
      ctx.bezierCurveTo(cp1.x, cp1.y, cp2.x, cp2.y, end.x, end.y);
      ctx.strokeStyle = `rgba(255, 174, 66, ${baseAlpha * 1.25})`;
      ctx.lineWidth = hoveredStreamIndex === idx ? 2.4 : 1.4;
      ctx.stroke();
    }
  });

  // 2. Update & draw flowing particles along the 7 trajectories
  particles.forEach(p => {
    const idx = p.streamIdx;
    const isHighlighted = hoveredStreamIndex === null || hoveredStreamIndex === idx;
    const speedMult = hoveredStreamIndex === idx ? 1.55 : 1.0;
    p.t += p.speed * speedMult * dt;
    if (p.t >= 1.0) {
      p.t -= 1.0;
      if (idx < 5 && hoveredStreamIndex === idx) {
        uniforms.uFlash.value = Math.min(1.0, uniforms.uFlash.value + 0.15);
      }
    }

    const start = starts[idx];
    if (!start) return;

    let pos;
    let alpha = hoveredStreamIndex === null ? 0.9 : (isHighlighted ? 1.0 : 0.12);

    if (idx < 5) {
      // Swallowed particle: accelerates as t -> 1 and spirals tightly into center
      const tAccel = Math.pow(p.t, 1.35);
      const cp1 = { x: start.x + (center.x - start.x) * 0.52, y: start.y };
      const cp2 = { x: center.x - 42, y: center.y + (idx - 2) * 18 };
      const basePos = cubicBezier(start, cp1, cp2, center, tAccel);

      // Add a tight inward swirl near the event horizon (t > 0.65)
      const swirlPhase = Math.max(0, (tAccel - 0.6) / 0.4);
      const swirlRadius = (1 - tAccel) * 28 * swirlPhase;
      const angle = swirlPhase * 9.0 + now * 4.0 + p.spiralOffset * 6.0;
      pos = {
        x: basePos.x + Math.cos(angle) * swirlRadius,
        y: basePos.y + Math.sin(angle) * swirlRadius,
      };
      // Fade out right as it crosses the black shadow at t > 0.92
      alpha *= Math.min(1.0, (1.0 - tAccel) * 12.0);

      ctx.beginPath();
      ctx.arc(pos.x, pos.y, p.size * (1 - swirlPhase * 0.4), 0, Math.PI * 2);
      ctx.fillStyle = `rgba(251, 113, 133, ${alpha})`;
      ctx.fill();
    } else {
      // Passed particle: slingshots around the black hole and arrives at Right Column
      const end = idx === 5 ? targetWorkspace : targetClipboard;
      const lensApexY = center.y + (idx === 5 ? 64 : 84);
      const cp1 = { x: center.x - 70, y: lensApexY };
      const cp2 = { x: center.x + 70, y: lensApexY };
      pos = cubicBezier(start, cp1, cp2, end, p.t);

      ctx.beginPath();
      ctx.arc(pos.x, pos.y, p.size * (hoveredStreamIndex === idx ? 1.3 : 1.0), 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255, 220, 150, ${alpha})`;
      ctx.fill();
    }
  });
}

// ============================================================================
// 3. Main Animation Loop
// ============================================================================
const clock = new THREE.Clock();
let prevTime = 0;

function animate() {
  requestAnimationFrame(animate);
  const now = clock.getElapsedTime();
  const dt = Math.min(now - prevTime, 0.05);
  prevTime = now;

  uniforms.uTime.value = now;
  uniforms.uAirgap.value += (targetAirgap - uniforms.uAirgap.value) * 0.08;
  uniforms.uFlash.value *= Math.pow(0.08, dt);

  renderer.render(scene, orthoCamera);
  drawStreams(dt, now);
}
animate();

// ============================================================================
// 4. Airgap Toggle, Copy Button & Terminal Tabs
// ============================================================================
const airgapBtn = document.getElementById('airgap-toggle');
const badgeText = document.getElementById('badge-status-text');
const outEgressCard = document.getElementById('out-card-egress');
const outEgressBadge = document.getElementById('out-egress-badge');
const outEgressVal = document.getElementById('out-egress-val');

airgapBtn.addEventListener('click', () => {
  targetAirgap = targetAirgap === 0 ? 1 : 0;
  document.body.classList.toggle('airgap-active', targetAirgap === 1);
  airgapBtn.classList.toggle('active', targetAirgap === 1);
  updateEgressUI();
});

function updateEgressUI() {
  if (targetAirgap === 1) {
    airgapBtn.textContent = currentLang === 'zh' ? '[ 模拟代理闪退 : 已锁死 ]' : '[ SIMULATE PROXY DROP : LOCKED ]';
    badgeText.textContent = 'BLACKHOLE // AIRGAP';
    outEgressCard.className = 'output-card blinded';
    outEgressBadge.textContent = 'BLACKHOLE_AIRGAP';
    outEgressVal.className = 'output-val highlight-red';
    outEgressVal.textContent = currentLang === 'zh'
      ? 'TUN 路由中断 -> 防火墙已自动锁死外网'
      : 'TUN Route Lost -> Outbound Locked by Guard';
  } else {
    airgapBtn.textContent = currentLang === 'zh' ? '[ 模拟代理闪退 : 关闭 ]' : '[ SIMULATE PROXY DROP : OFF ]';
    badgeText.textContent = 'BLACKHOLE';
    outEgressCard.className = 'output-card verified';
    outEgressBadge.textContent = 'FAIL-CLOSE';
    outEgressVal.className = 'output-val highlight-green';
    outEgressVal.textContent = currentLang === 'zh'
      ? 'utun0 隧道已校验 · IPv6 已彻底禁用'
      : 'utun0 Verified · IPv6 Disabled';
  }
}

const copyBtn = document.getElementById('copy-install-btn');
const installCmd = 'git clone https://github.com/tommy0103/blackhole.git';
copyBtn.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(installCmd);
    const prev = copyBtn.textContent;
    copyBtn.textContent = 'COPIED';
    setTimeout(() => { copyBtn.textContent = prev; }, 1800);
  } catch {}
});

const terminalScenarios = {
  launch: `<span class="ansi-dim">$</span> cd ~/Code/quantum-engine && claude
<span class="ansi-badge"> BLACKHOLE </span> <span class="ansi-magenta">Colima VM isolated session · work</span>
<span class="ansi-cyan">/Users/you/Code/quantum-engine</span> <span class="ansi-dim">(only this directory is mounted)</span>

<span class="ansi-magenta">&gt;</span> Verify our isolation boundary and run the test suite.

<span class="ansi-green">[EXEC]</span> Bash(hostname && df -T /workspace && npm test)
  ⎿  blackhole
     Filesystem     Type     Mounted on
     lima-virtiofs  virtiofs /workspace
     <span class="ansi-green">PASS</span> src/engine.test.ts (42 tests in 0.84s)`,

  status: `<span class="ansi-dim">$</span> blackhole status
Colima profile: work
Claude launcher: <span class="ansi-green">protected</span> (/Users/you/.local/bin/claude)
Colima:         <span class="ansi-green">running</span>
Image:          blackhole-claude:project-9f3a1c8b02e4
Config volume:  blackhole-claude-home-work
Active sessions:
  <span class="ansi-cyan">blackhole-work-51892</span>  /Users/you/Code/quantum-engine

<span class="ansi-dim">$</span> blackhole profiles
PROFILE   STATUS   MEMORY  BLACKHOLE  PROJECTS  SESSIONS  LAST USED
default   Running  8GiB    yes        4         19        2026-11-08 11:20
work      Running  16GiB   yes        2         7         2026-11-08 12:50`,

  netwatch: `<span class="ansi-dim">$</span> blackhole net ipv6 off --all && blackhole net-watch start
blackhole: <span class="ansi-yellow">fail-close: blocking network before starting watcher</span>
blackhole: net-watch running (pid 64120), fail-close
blackhole: probe: https://www.google.com/generate_204 (require utun: 1)

2026-11-08 12:51:04 <span class="ansi-green">probe=up; restoring network</span>
2026-11-08 12:52:19 <span class="ansi-red">probe=down (utun route lost); blocking network</span>
2026-11-08 12:52:19 <span class="ansi-red">BLACKHOLE_AIRGAP applied across OUTPUT, FORWARD, DOCKER-USER</span>`,

  resume: `<span class="ansi-dim">$</span> blackhole sessions
PROFILE  SESSION                               PATH                             STATUS
work     89e4f9ec-8960-4175-9bb0-4bbea4780fa9  /Users/you/Code/quantum-engine   <span class="ansi-green">active</span>
default  3c71a04b-112e-49c0-8a11-9d02f18a6201  /Users/you/Code/shader-lab       ended

<span class="ansi-dim">$</span> blackhole resume 3c71
blackhole: resuming 3c71a04b-112e-49c0-8a11-9d02f18a6201 on profile 'default' in /Users/you/Code/shader-lab`
};

const terminalBody = document.getElementById('terminal-body');
function setTerminalTab(key) {
  terminalBody.innerHTML = terminalScenarios[key];
}
document.querySelectorAll('.term-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.term-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    setTerminalTab(tab.dataset.tab);
  });
});
setTerminalTab('launch');

// ============================================================================
// 5. Bilingual Switcher (EN <-> 中文)
// ============================================================================
const i18n = {
  en: {
    nav_blog: 'BLOG',
    eyebrow: 'macOS Colima Sandbox // Zero Host Fingerprint',
    hero_title: 'Swallow Host Fingerprints. Pass Only /workspace.',
    hero_sub: 'When you type claude on your Mac, Blackhole intercepts the launch. Hardware serials, region settings, and SSH keys are swallowed into the singularity — only your current folder slingshots across into an isolated Colima VM.',
    col_left_title: '01. YOUR MAC HOST',
    col_left_sub: 'RAW ENV',
    s_item_0: 'Hardware UUID (ioreg)',
    s_item_1: 'Region & Locale (zh_CN)',
    s_item_2: '~/.ssh & Cloud Keys',
    s_item_3: 'Other Repos (~/Code/*)',
    s_item_4: 'Direct ISP / IPv6 IP',
    s_item_5: 'Current Dir ($PWD)',
    s_item_6: 'Pasted Image ($TMPDIR)',
    fate_drop: 'SWALLOWED',
    fate_pass: 'SLINGSHOT',
    center_caption: 'Hover any stream on the left · <strong>5 swallowed</strong> into horizon · <strong>2 lensed</strong> into VM',
    col_right_title: '02. COLIMA VM CONTAINER',
    col_right_sub: 'CLAUDE\'S VIEW',
    out_0_label: 'HOST FINGERPRINT',
    out_0_val: 'Undetectable (--hostname blackhole)',
    out_1_label: 'PROJECT MOUNT',
    out_1_val: '/workspace (UID/GID matched rw)',
    out_2_label: 'CLIPBOARD BRIDGE',
    out_2_val: '$TMPDIR/*.png mirrored in 0.5s',
    out_3_label: 'NETWORK EGRESS',
    out_3_val: 'utun0 Verified · IPv6 Disabled',
    legend_red: 'Red stream: Swallowed into Event Horizon (Never reaches Claude)',
    legend_gold: 'Gold stream: Lensed across Horizon into Colima VM',
    s2_tag: '01 // TELEMETRY',
    s2_title: 'Same claude Command. Different Universe.',
    s3_tag: '02 // VELOCITY',
    s3_title: 'Live Two-Way Sync Over virtiofs.',
    stat1_label: 'Sequential Read',
    stat1_sub: '64 MB read in 20.1 ms across the VM boundary via Apple Virtualization virtiofs.',
    stat2_label: 'Sequential Write (fsync)',
    stat2_sub: 'Immediate bi-directional visibility between host editor and container tools.',
    stat3_label: 'Per-File Metadata Stat',
    stat3_sub: '1,000 files traversed in 33.9 ms — full ripgrep and git status without lag.',
  },
  zh: {
    nav_blog: '博客',
    eyebrow: 'macOS Colima 隔离沙盒 // 宿主机指纹归零',
    hero_title: '吞噬宿主机指纹，只让 /workspace 穿越视界。',
    hero_sub: '当你在 Mac 上敲下 claude，Blackhole 会在视界处拦截启动：硬件序列号、地区语言设置与 SSH 私钥全部坠入黑洞湮灭；唯有当前项目目录借引力弹弓进入隔离的 Colima 容器。',
    col_left_title: '01. 你的 MAC 宿主机',
    col_left_sub: '原始环境',
    s_item_0: '硬件序列号 (ioreg UUID)',
    s_item_1: '系统地区与语言 (zh_CN)',
    s_item_2: '~/.ssh 与云厂商密钥',
    s_item_3: '其他代码仓库 (~/Code/*)',
    s_item_4: '真实宽带 IP / IPv6',
    s_item_5: '当前项目目录 ($PWD)',
    s_item_6: '剪贴板截图 ($TMPDIR)',
    fate_drop: '坠入黑洞',
    fate_pass: '穿透视界',
    center_caption: '悬停左侧任意数据流 · <strong>5 项宿主机特征被吞噬</strong> · <strong>2 项工作流穿透至容器</strong>',
    col_right_title: '02. COLIMA 隔离容器内',
    col_right_sub: 'CLAUDE 视角',
    out_0_label: '宿主机环境指纹',
    out_0_val: '完全不可见 (--hostname blackhole)',
    out_1_label: '项目目录挂载',
    out_1_val: '/workspace (UID/GID 对齐 · 实时读写)',
    out_2_label: '剪贴板贴图桥接',
    out_2_val: '$TMPDIR/*.png 0.5 秒只读镜像',
    out_3_label: '网络出口守卫',
    out_3_val: 'utun0 隧道已校验 · IPv6 已禁用',
    legend_red: '红色粒子流：坠入黑洞视界湮灭（Claude 永远无法读取）',
    legend_gold: '金色粒子流：经引力弹弓折射进入 Colima 虚拟机容器',
    s2_tag: '01 // 终端实况',
    s2_title: '还是敲 claude，但已在另一个宇宙。',
    s3_tag: '02 // 同步速度',
    s3_title: '基于 virtiofs 的实时双向同步。',
    stat1_label: '顺序读取吞吐',
    stat1_sub: '基于 Apple Virtualization virtiofs，跨虚拟机读取 64 MB 仅需 20.1 毫秒。',
    stat2_label: '顺序写入吞吐 (含 fsync)',
    stat2_sub: '宿主机编辑器与容器内工具双向秒级可见，无需定时拷贝。',
    stat3_label: '单文件元数据 Stat',
    stat3_sub: '遍历 1,000 个小文件仅需 33.9 毫秒，全库 ripgrep 与 git status 毫无卡顿。',
  }
};

const langBtn = document.getElementById('lang-toggle');
langBtn.addEventListener('click', () => {
  currentLang = currentLang === 'en' ? 'zh' : 'en';
  langBtn.textContent = currentLang === 'en' ? '中文' : 'EN';
  const dict = i18n[currentLang];
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (dict[key]) el.innerHTML = dict[key];
  });
  updateEgressUI();
});
