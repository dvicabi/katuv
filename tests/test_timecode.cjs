const assert=require('node:assert/strict');
const time=require('../autocaption/web_static/timecode.js');
assert.equal(time.format(206.16),'03:26.16');
assert.equal(time.parse('03:26.16'),206.16);
assert.equal(time.format(59.995),'01:00.00');
assert.equal(time.format(3599.999),'60:00.00');
assert.equal(time.format(0),'00:00.00');
assert.equal(time.parse('3:26,1'),206.1);
assert.equal(time.parse(' 03:26 '),206);
assert.equal(time.parse('00:00.01'),.01);
assert.equal(time.parse('100:59.99'),6059.99);
for(const invalid of ['', '03:', '03:6.16', '03:60.00','03:26.166','-1:00.00','206.16','1:02:03.00','שלום','Infinity'])assert.ok(Number.isNaN(time.parse(invalid)),invalid);
for(const invalid of [null,undefined,NaN,Infinity,-1])assert.equal(time.format(invalid),'');
const field={value:'',removeAttribute(){}};
time.write(field,33.602);assert.equal(field.value,'00:33.60');assert.equal(time.read(field),33.602);
field.value='00:34.26';assert.equal(time.read(field),34.26);
field.value='00:33.60';assert.equal(time.read(field),33.602); // untouched canonical display retains the anchor
time.write(field,null);assert.equal(field.value,'');assert.ok(Number.isNaN(time.read(field)));
field.value='00:33.60';assert.equal(time.read(field,33.602,40),33.602);
field.value='04:15.94';assert.equal(time.read(field,0,255.9391),255.9391);
field.value='04:16.00';assert.equal(time.read(field,0,255.9391),256); // Out of bounds stays invalid, never silently clamped.
field.value='03:';assert.ok(Number.isNaN(time.read(field)));
for(const options of [{tag:'BUTTON'},{tag:'INPUT',type:'range'},{tag:'INPUT',type:'text',timecode:true},{tag:'DIV'}])assert.equal(time.canTogglePlayback(options),true);
for(const options of [{tag:'TEXTAREA'},{tag:'SELECT'},{tag:'INPUT',type:'text'},{tag:'INPUT',type:'checkbox'},
 {tag:'INPUT',type:'radio'},{tag:'DIV',editable:true},{tag:'BUTTON',modal:true},{tag:'BUTTON',modified:true},{composing:true}])assert.equal(time.canTogglePlayback(options),false);
console.log('Timecode round trips, exact anchors, bounds and playback focus contexts passed.');
