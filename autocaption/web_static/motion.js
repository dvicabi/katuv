"use strict";
// The numbers come from /api/motion (the same presets used by the ASS renderer).
const CaptionMotion = (() => {
  const clamp = value => Math.min(1, Math.max(0, value));
  const round = value => Math.floor(value + .500000001);
  const cs = value => round(value * 100) / 100;
  const customCache=new WeakMap();
  function customize(spec, settings) {
    if (!spec) return null;
    const strength = settings.motion_strength ?? 1, speed = settings.motion_speed ?? 1;
    const animated=settings.word_animation!==false&&settings.caption_mode!=="segments";
    const key=strength+":"+speed+":"+animated,previous=customCache.get(spec);
    if(previous?.key===key)return previous.value;
    const out = {...spec, pop_scale:100+(spec.pop_scale-100)*strength,
      pulse:spec.pulse.map(([t,size,ease])=>[t,100+(size-100)*strength,ease]),
      entry_y:spec.entry_y*strength, exit_y:spec.exit_y*strength};
    for(const key of ['pop_ms','entry_ms','exit_ms','fade_in_ms','fade_out_ms','color_ms']) out[key]=Math.round(spec[key]/speed);
    if(!animated){out.pop_scale=100;out.pulse=[[0,100,1],[1,100,1]];out.pop_ms=0;}
    customCache.set(spec,{key,value:out});return out;
  }
  function layout(lengths, space, requested, width, height, settings, spec) {
    const peak=(lengths.reduce((a,b)=>a+b,0)+(lengths.length-1)*space+Math.max(0,...lengths)*(spec.pop_scale/100-1))*(1000/1360);
    const angle=(settings.rotation||0)*Math.PI/180,c=Math.abs(Math.cos(angle)),s=Math.abs(Math.sin(angle));
    const boundW=c*peak+s*requested*spec.pop_scale/100,boundH=s*peak+c*requested*spec.pop_scale/100;
    const size=Math.max(1,Math.min(requested,Math.floor(requested*width*.88/Math.max(1,boundW)),Math.floor(requested*height*.80/Math.max(1,boundH))));
    const ratio=size/requested,padding=settings.backdrop_opacity>0?size*.18:0;
    const margin=(settings.outline+1.5)*height/1920+Math.min(width,height)*.015+padding*(c+s);
    const halfW=Math.min(width/2,boundW*ratio/2+margin),halfH=boundH*ratio/2+margin;
    const top=Math.min(height/2,halfH+Math.max(0,-spec.exit_y)*height/1920),bottom=Math.min(height/2,halfH+Math.max(0,spec.entry_y)*height/1920);
    return {size,x:Math.max(halfW,Math.min(width-halfW,width*(settings.position_x??.5))),
      y:Math.max(top,Math.min(height-bottom,height*settings.position)),boxW:peak*ratio,boxH:size*spec.pop_scale/100,padding};
  }
  function pulseFrames(spec, duration) {
    const length = Math.min(spec.pop_ms, round(duration*.78)),frames=new Map([[0,100]]);
    if(length<8)return [...frames];
    for(let i=1;i<spec.pulse.length;i++){
      const [a,start]=spec.pulse[i-1],[b,end]=spec.pulse[i];
      for(let step=1;step<=12;step++){
        const t=step/12,time=round((a+(b-a)*t)*length);
        if(time>0)frames.set(time,Math.round((start+(end-start)*t*t*(3-2*t))*1e5)/1e5);
      }
    }
    return [...frames].sort((a,b)=>a[0]-b[0]);
  }
  function blockLayout(widths,requested,width,height,settings,spec){
    const textW=Math.max(0,...widths)*1000/1360,textH=widths.length*requested;
    const angle=(settings.rotation||0)*Math.PI/180,c=Math.abs(Math.cos(angle)),s=Math.abs(Math.sin(angle));
    const boundW=c*textW+s*textH,boundH=s*textW+c*textH;
    const size=Math.max(1,Math.min(requested,Math.floor(requested*width*.88/Math.max(1,boundW)),Math.floor(requested*height*.80/Math.max(1,boundH))));
    const ratio=size/requested,padding=settings.backdrop_opacity>0?size*.18:0;
    const margin=(settings.outline+1.5)*height/1920+Math.min(width,height)*.015+padding*(c+s);
    const halfW=Math.min(width/2,boundW*ratio/2+margin),halfH=boundH*ratio/2+margin;
    const top=Math.min(height/2,halfH+Math.max(0,-spec.exit_y)*height/1920),bottom=Math.min(height/2,halfH+Math.max(0,spec.entry_y)*height/1920);
    return {size,x:Math.max(halfW,Math.min(width-halfW,width*(settings.position_x??.5))),y:Math.max(top,Math.min(height-bottom,height*settings.position)),boxW:textW*ratio,boxH:textH*ratio,padding};
  }
  const frameCache=new WeakMap();
  function pulse(spec, elapsed, duration) {
    const length = Math.min(spec.pop_ms, round(duration * .78));
    if (length < 8 || elapsed >= length || elapsed < 0) return 1;
    let cache=frameCache.get(spec);if(!cache){cache=new Map();frameCache.set(spec,cache);}
    if(!cache.has(duration))cache.set(duration,pulseFrames(spec,duration));
    let previousTime = 0, previousSize = 100;
    for (const [target, size] of cache.get(duration).slice(1)) {
      if (elapsed <= target) {
        const progress = clamp((elapsed - previousTime) / (target - previousTime));
        return (previousSize + (size - previousSize) * progress) / 100;
      }
      previousTime = target; previousSize = size;
    }
    return 1;
  }
  function colorAmount(spec, elapsed, duration) {
    if (elapsed < 0 || elapsed >= duration) return 0;
    const fade = Math.max(1, Math.min(spec.color_ms, Math.round(duration * .2)));
    if (elapsed < fade) return (elapsed / fade) ** .7;
    if (elapsed > duration - fade) return 1 - ((elapsed - duration + fade) / fade) ** 1.3;
    return 1;
  }
  function mix(base, highlight, amount) {
    const channels = [1, 3, 5].map(i => {
      const a = parseInt(base.slice(i, i + 2), 16), b = parseInt(highlight.slice(i, i + 2), 16);
      return Math.round(a + (b - a) * clamp(amount));
    });
    return `rgb(${channels.join(",")})`;
  }
  function line(spec, rows, time) {
    const first = rows[0], last = rows.at(-1);
    const start = cs(first.start), end = cs(last.end);
    if (time < start || time >= end) return {opacity:0, y:0};
    const enter = Math.min(spec.entry_ms, Math.max(1, Math.round((first.end - first.start) * 450)));
    const leave = Math.min(spec.exit_ms, Math.max(1, Math.round((last.end - last.start) * 350)));
    const fadeIn = Math.min(spec.fade_in_ms, enter), fadeOut = Math.min(spec.fade_out_ms, leave);
    const elapsed = (time - start) * 1000, remaining = (end - time) * 1000;
    const opacity = Math.min(fadeIn ? clamp(elapsed / fadeIn) : 1, fadeOut ? clamp(remaining / fadeOut) : 1);
    let y = spec.entry_y * (1 - clamp(elapsed / enter));
    // libass supports one move per event: karaoke and one-word lines use entrance only.
    if (spec.mode !== "karaoke" && rows.length > 1 && remaining < leave) y = spec.exit_y * (1 - remaining / leave);
    return {opacity, y};
  }
  return {pulse, pulseFrames, colorAmount, mix, line, cs, customize, layout, blockLayout};
})();
if (typeof module !== "undefined") module.exports = CaptionMotion;
