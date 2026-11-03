// Pure timing math, using the actual Python preset contract exported by the test.
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {resolve} = require('node:path');
const motion = require('../autocaption/web_static/motion.js');
const editing = require('../autocaption/web_static/editing.js');
const python = process.env.AUTOCAPTION_TEST_PYTHON || (process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const specs = JSON.parse(execFileSync(resolve(python), ['-c', 'import json; from autocaption.styles import PRESETS,motion_spec; print(json.dumps({k:motion_spec(v) for k,v in PRESETS.items()}))'], {encoding:'utf8'}));
const exportedFrames = JSON.parse(execFileSync(resolve(python), ['-c', 'import json; from autocaption.styles import PRESETS,pulse_frames; print(json.dumps({k:{d:pulse_frames(s,d) for d in (10,50,180,600,625)} for k,s in PRESETS.items()}))'], {encoding:'utf8'}));
for(const [name,spec] of Object.entries(specs))for(const duration of [10,50,180,600,625]){
 const expected=exportedFrames[name][duration],actual=motion.pulseFrames(spec,duration);
 assert.equal(actual.length,expected.length);
 for(let i=0;i<actual.length;i++){assert.equal(actual[i][0],expected[i][0]);assert(Math.abs(actual[i][1]-expected[i][1])<.00002);}
 assert(motion.pulse(spec,1,600)-1<.01,'Pulse must begin gently, without a velocity jump');
}
assert.equal(motion.cs(.125),.13);assert.equal(motion.cs(.145),.15);
assert.deepEqual(editing.edge(.5,.8,'start',.1,.4,1),{start:.4,end:.8});
assert.deepEqual(editing.edge(.5,.8,'end',2,.4,1),{start:.5,end:1});
assert.deepEqual(editing.edge(.5,.8,'start',.8,.4,1),{start:.79,end:.8});
assert.equal(editing.edge(.5,.8,'start',.6,.8,1),null);
assert.deepEqual(editing.shift(.5,.8,-.5,.4,1),{start:.4,end:.7});
assert.deepEqual(editing.shift(.5,.8,.5,.4,1),{start:.7,end:1});
assert.equal(editing.shift(.5,.8,.1,.6,.7),null);
assert.equal(editing.snap(.51,6,300),.5);assert.equal(editing.snap(.51,6,300,false),.51);
for(const spec of Object.values(specs)) {
  for(const duration of [10, 50, 180, 600]) {
    assert.equal(motion.pulse(spec, 0, duration), 1);
    assert.equal(motion.pulse(spec, duration, duration), 1);
    assert.equal(motion.colorAmount(spec, duration, duration), 0);
    for(let t=0;t<duration;t++) {
      const size=motion.pulse(spec,t,duration);
      assert(size >= .95 && size <= spec.pop_scale/100+.001);
      const color=motion.colorAmount(spec,t,duration);
      assert(color>=0 && color<=1);
    }
  }
  const rows=[{start:0,end:.6},{start:.8,end:1.4}];
  assert.equal(motion.line(spec,rows,.7).opacity,1); // no blink during a gap
  assert.equal(motion.line(spec,rows,-.01).opacity,0);
  assert.equal(motion.line(spec,rows,1.4).opacity,0);
}
assert.equal(motion.mix('#FFFFFF','#FFD700',1),'rgb(255,215,0)');
for(const strength of [0,.5,1,2])for(const speed of [.5,1,2]){
 const settings={motion_strength:strength,motion_speed:speed,position_x:.96,position:.04,rotation:45,outline:3};
 const spec=motion.customize(specs.pop,settings);
 assert.equal(spec.pop_scale,100+27*strength);
 assert.equal(spec.pop_ms,Math.round(220/speed));
 assert.equal(motion.pulse(spec,600,600),1);
 const box=motion.layout([180,120],30,90,540,960,settings,spec);
 assert(box.x<540 && box.y>0 && box.size<=90);
}
console.log('Shared motion: all 4 styles, short words, exact boundaries and gaps passed.');
