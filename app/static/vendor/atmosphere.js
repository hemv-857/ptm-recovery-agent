/* ── Plasma + Cursor Ribbons — zero deps, two canvases ── */
(function(){
  "use strict";
  if(window.matchMedia("(prefers-reduced-motion: reduce)").matches)return;

  /* ═══ 1. WebGL Plasma — THE background, not a tint ═══ */
  var pc=document.createElement("canvas");
  pc.style.cssText="position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none";
  document.body.prepend(pc);
  var gl=pc.getContext("webgl")||pc.getContext("experimental-webgl");
  if(gl){
    var vs="attribute vec2 p;void main(){gl_Position=vec4(p,0,1);}";
    var fs=[
      "precision highp float;",
      "uniform float t;uniform vec2 r;",
      // Classic plasma — layered sine waves in navy/cyan/teal
      "float plasma(vec2 uv,float time){",
      "  float v=0.0;",
      "  v+=sin(uv.x*3.14159+time*0.37);",
      "  v+=sin(uv.y*3.14159-time*0.29);",
      "  v+=sin((uv.x+uv.y)*2.22+time*0.53);",
      "  v+=sin(length(uv-vec2(0.5))*6.0-time*0.41)*0.5;",
      "  v+=sin(uv.x*5.0-time*0.67)*cos(uv.y*4.0+time*0.49)*0.35;",
      "  return v/3.5;",
      "}",
      "void main(){",
      "  vec2 uv=gl_FragCoord.xy/r;",
      "  float p=plasma(uv,t);",
      // Map plasma [-1,1] to navy→cyan→teal
      "  vec3 navy=vec3(0.02,0.10,0.28);",
      "  vec3 cyan=vec3(0.0,0.55,0.80);",
      "  vec3 teal=vec3(0.20,0.65,0.55);",
      "  vec3 dark=vec3(0.04,0.06,0.10);",
      "  vec3 col=dark;",
      "  col=mix(col,navy,smoothstep(-0.6,0.0,p)*0.7);",
      "  col=mix(col,cyan,smoothstep(-0.1,0.5,p)*0.55);",
      "  col=mix(col,teal,smoothstep(0.3,0.9,p)*0.4);",
      // Vignette
      "  float vig=1.0-length(uv-vec2(0.5))*1.1;",
      "  col*=smoothstep(0.0,0.5,vig);",
      "  gl_FragColor=vec4(col,1.0);",
      "}"
    ].join("\n");
    function cs(src,t){var s=gl.createShader(t);gl.shaderSource(s,src);gl.compileShader(s);return s;}
    var pg=gl.createProgram();
    gl.attachShader(pg,cs(vs,gl.VERTEX_SHADER));
    gl.attachShader(pg,cs(fs,gl.FRAGMENT_SHADER));
    gl.linkProgram(pg);gl.useProgram(pg);
    var buf=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buf);
    gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);
    var pL=gl.getAttribLocation(pg,"p");gl.enableVertexAttribArray(pL);gl.vertexAttribPointer(pL,2,gl.FLOAT,false,0,0);
    var uT=gl.getUniformLocation(pg,"t"),uR=gl.getUniformLocation(pg,"r");
    var t0=performance.now();
    (function loop(){
      pc.width=devicePixelRatio*innerWidth|0;pc.height=devicePixelRatio*innerHeight|0;
      gl.viewport(0,0,pc.width,pc.height);
      gl.uniform1f(uT,(performance.now()-t0)*.001);
      gl.uniform2f(uR,pc.width,pc.height);
      gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
      requestAnimationFrame(loop);
    })();
  }

  /* ═══ 2. Cursor ribbons — large, visible, layered ═══ */
  var rc=document.getElementById("tubes-cursor-canvas")||document.createElement("canvas");
  if(!rc.id){rc.id="tubes-cursor-canvas";rc.style.cssText="position:fixed;inset:0;width:100%;height:100%;z-index:2;pointer-events:none";document.body.appendChild(rc);}
  var ctx=rc.getContext("2d");
  var mx=-9999,my=-9999,lmx=-9999,lmy=-9999;
  var trails=[];
  var COLORS=["#6dc3f0","#57d8b4"];

  function resize(){rc.width=devicePixelRatio*innerWidth|0;rc.height=devicePixelRatio*innerHeight|0;ctx.scale(devicePixelRatio,devicePixelRatio);}
  window.addEventListener("resize",resize);resize();

  document.addEventListener("mousemove",function(e){
    lmx=mx;lmy=my;mx=e.clientX;my=e.clientY;
    var dx=mx-lmx,dy=my-lmy,speed=Math.sqrt(dx*dx+dy*dy);
    if(speed<1)return;
    var n=Math.min(Math.ceil(speed/4),6);
    for(var i=0;i<n;i++){
      trails.push({
        x:mx+(Math.random()-.5)*8,y:my+(Math.random()-.5)*8,
        vx:dx*.06*(Math.random()-.2),vy:dy*.06*(Math.random()-.2),
        life:1,decay:.006+Math.random()*.006,
        r:3+Math.random()*4,
        color:COLORS[i%2]
      });
    }
    while(trails.length>50)trails.shift();
  });

  (function draw(){
    ctx.clearRect(0,0,rc.width/devicePixelRatio,rc.height/devicePixelRatio);
    for(var i=trails.length-1;i>=0;i--){
      var t=trails[i];
      t.x+=t.vx;t.y+=t.vy;t.vx*=.97;t.vy*=.97;t.life-=t.decay;
      if(t.life<=0){trails.splice(i,1);continue;}
      // Outer glow (big, soft)
      ctx.globalAlpha=t.life*.12;
      ctx.beginPath();ctx.arc(t.x,t.y,t.r*t.life*6,0,Math.PI*2);
      ctx.fillStyle=t.color;ctx.fill();
      // Mid glow
      ctx.globalAlpha=t.life*.3;
      ctx.beginPath();ctx.arc(t.x,t.y,t.r*t.life*3,0,Math.PI*2);
      ctx.fillStyle=t.color;ctx.fill();
      // Core
      ctx.globalAlpha=t.life*.85;
      ctx.beginPath();ctx.arc(t.x,t.y,t.r*t.life,0,Math.PI*2);
      ctx.fillStyle=t.color;ctx.fill();
    }
    ctx.globalAlpha=1;
    requestAnimationFrame(draw);
  })();
})();
