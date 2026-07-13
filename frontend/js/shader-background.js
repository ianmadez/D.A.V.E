/**
 * WebGL Warp Shader Background
 * Approximates @paper-design/shaders-react Warp component using raw WebGL.
 * Animated color fields warped by noise and swirls, applied over a check pattern.
 */
(function() {
    var canvas = document.getElementById('shader-canvas');
    if (!canvas) return;

    var gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: false });
    if (!gl) return;

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
        gl.viewport(0, 0, canvas.width, canvas.height);
    }
    window.addEventListener('resize', resize);
    resize();

    // Vertex shader
    var vs = [
        'attribute vec2 a_position;',
        'void main() {',
        '    gl_Position = vec4(a_position, 0.0, 1.0);',
        '}'
    ].join('\n');

    // Fragment shader
    var fs = [
        'precision mediump float;',
        'uniform vec2 u_resolution;',
        'uniform float u_time;',
        '',
        'float random(vec2 st) {',
        '    return fract(sin(dot(st.xy, vec2(12.9898, 78.233))) * 43758.5453123);',
        '}',
        '',
        'float noise(vec2 st) {',
        '    vec2 i = floor(st);',
        '    vec2 f = fract(st);',
        '    float a = random(i);',
        '    float b = random(i + vec2(1.0, 0.0));',
        '    float c = random(i + vec2(0.0, 1.0));',
        '    float d = random(i + vec2(1.0, 1.0));',
        '    vec2 u = f * f * (3.0 - 2.0 * f);',
        '    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);',
        '}',
        '',
        'void main() {',
        '    vec2 uv = gl_FragCoord.xy / u_resolution;',
        '    float aspect = u_resolution.x / u_resolution.y;',
        '    vec2 p = uv * 2.0 - 1.0;',
        '    p.x *= aspect;',
        '    float t = u_time * 0.25;',
        '    float n1 = noise(p * 1.5 + t * 0.5);',
        '    float n2 = noise(p * 3.0 - t * 0.3);',
        '    float angle = n1 * 6.2832;',
        '    float dist = 0.25;',
        '    p.x += dist * n2 * cos(angle);',
        '    p.y += dist * n2 * sin(angle);',
        '    float sw = 0.8;',
        '    for (int i = 1; i <= 10; i++) {',
        '        float fi = float(i);',
        '        p.x += sw / fi * cos(t + fi * 1.5 * p.y);',
        '        p.y += sw / fi * cos(t + fi * 1.0 * p.x);',
        '    }',
        '    float scale = 0.1;',
        '    float checks = sin(p.x * scale * 3.14159 * 4.0) * cos(p.y * scale * 3.14159 * 4.0);',
        '    checks = checks * 0.5 + 0.5;',
        '    float blend = checks * 0.7 + n1 * 0.3;',
        '    vec3 c0 = vec3(0.741, 0.678, 0.678);',
        '    vec3 c1 = vec3(0.224, 0.067, 0.690);',
        '    vec3 c2 = vec3(0.698, 0.643, 0.643);',
        '    vec3 c3 = vec3(0.420, 0.310, 0.549);',
        '    float proportion = 0.45;',
        '    float softness = 1.0;',
        '    float pos = blend;',
        '    vec3 col;',
        '    if (pos < proportion) {',
        '        float mixVal = pos / proportion;',
        '        col = mix(c0, c1, smoothstep(0.0, softness, mixVal));',
        '    } else if (pos < proportion * 2.0) {',
        '        float mixVal = (pos - proportion) / proportion;',
        '        col = mix(c1, c2, smoothstep(0.0, softness, mixVal));',
        '    } else {',
        '        float mixVal = (pos - proportion * 2.0) / (1.0 - proportion * 2.0);',
        '        col = mix(c2, c3, smoothstep(0.0, softness, mixVal));',
        '    }',
        '    gl_FragColor = vec4(col, 1.0);',
        '}'
    ].join('\n');

    function compileShader(src, type) {
        var s = gl.createShader(type);
        gl.shaderSource(s, src);
        gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
            console.error('Shader error:', gl.getShaderInfoLog(s));
            return null;
        }
        return s;
    }

    var program = gl.createProgram();
    gl.attachShader(program, compileShader(vs, gl.VERTEX_SHADER));
    gl.attachShader(program, compileShader(fs, gl.FRAGMENT_SHADER));
    gl.linkProgram(program);

    var posLoc = gl.getAttribLocation(program, 'a_position');
    var resLoc = gl.getUniformLocation(program, 'u_resolution');
    var timeLoc = gl.getUniformLocation(program, 'u_time');

    var quad = new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]);
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, quad, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(posLoc);
    gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);

    gl.useProgram(program);

    var startTime = performance.now();

    function render() {
        var elapsed = (performance.now() - startTime) / 1000;
        gl.uniform2f(resLoc, canvas.width, canvas.height);
        gl.uniform1f(timeLoc, elapsed);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        requestAnimationFrame(render);
    }

    render();
})();
