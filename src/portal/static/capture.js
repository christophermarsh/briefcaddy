// Local heuristic v1. No OCR, network, automatic shutter or readability verdict.
const CaptureVision = (() => {
  const policy = 'local-paper-v1';
  const cross = (a, b, c) => (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
  const distance = (a,b) => Math.hypot(a[0]-b[0], a[1]-b[1]);
  const validAnalysis = image => Number.isInteger(image.width) && Number.isInteger(image.height) && image.width >= 2 && image.height >= 2 && image.width*image.height <= 102400 && image.data?.length === image.width*image.height*4;
  function validCorners(p) {
    return Array.isArray(p) && p.length === 4 && p.every(q => Array.isArray(q) && q.length === 2 && q.every(v => Number.isFinite(v) && v >= 0 && v <= 1)) &&
      p.every((q,i) => cross(q,p[(i+1)%4],p[(i+2)%4]) > .002) &&
      p.reduce((s,q,i) => s+q[0]*p[(i+1)%4][1]-q[1]*p[(i+1)%4][0],0) > .04;
  }
  function hull(points) {
    points.sort((a,b) => a[0]-b[0] || a[1]-b[1]);
    const half = list => { const h=[]; for (const p of list) { while(h.length>1 && cross(h.at(-2),h.at(-1),p)<=0) h.pop(); h.push(p); } return h; };
    return half(points).slice(0,-1).concat(half(points.slice().reverse()).slice(0,-1));
  }
  function detect(image) {
    const {width:w,height:h,data:d}=image;
    if (!validAnalysis(image)) throw Error('analysis_size');
    const gray=new Float32Array(w*h);
    for(let i=0;i<gray.length;i++) gray[i]=.299*d[4*i]+.587*d[4*i+1]+.114*d[4*i+2];
    const corners=[gray[0],gray[w-1],gray[(h-1)*w],gray[w*h-1]].sort((a,b)=>a-b);
    const threshold=Math.max(110, (corners[1]+corners[2])/2+28);
    const seen=new Uint8Array(w*h), queue=new Int32Array(w*h); let best=[],bestSize=0;
    for(let seed=0;seed<gray.length;seed++) {
      if(seen[seed] || gray[seed]<threshold) continue;
      let head=0,tail=1;queue[0]=seed;seen[seed]=1;const boundary=[];
      while(head<tail) {
        const i=queue[head++],x=i%w,y=Math.floor(i/w);let edge=false;
        for(const [nx,ny] of [[x-1,y],[x+1,y],[x,y-1],[x,y+1]]) {
          if(nx<0||ny<0||nx>=w||ny>=h){edge=true;continue;}
          const ni=ny*w+nx;
          if(gray[ni]<threshold){edge=true;continue;}
          if(!seen[ni]){seen[ni]=1;queue[tail++]=ni;}
        }
        if(edge) boundary.push([x/(w-1),y/(h-1)]);
      }
      if(tail>w*h*.12 && tail<w*h*.96 && tail>bestSize){best=boundary;bestSize=tail;}
    }
    if(!best.length)return null;
    const p=hull(best);
    while(p.length>4) {
      let k=0,least=Infinity;
      for(let i=0;i<p.length;i++){const area=Math.abs(cross(p[(i+p.length-1)%p.length],p[i],p[(i+1)%p.length]));if(area<least){least=area;k=i;}}
      p.splice(k,1);
    }
    const first=p.reduce((k,q,i)=>q[0]+q[1]<p[k][0]+p[k][1]?i:k,0);
    const ordered=p.slice(first).concat(p.slice(0,first));
    return validCorners(ordered)?ordered:null;
  }
  function quality(image,p,originalWidth,originalHeight) {
    const {width:w,height:h,data:d}=image;
    if(!validAnalysis(image))throw Error('analysis_size');
    if(!Number.isInteger(originalWidth)||!Number.isInteger(originalHeight)||originalWidth<2||originalHeight<2||originalWidth*originalHeight>12000000 || (p!==null&&!validCorners(p)))throw Error('invalid_quality_geometry');
    const flags=[];let n=0,sum=0,sq=0,bright=0,dark=0;
    const gray=i => .299*d[4*i]+.587*d[4*i+1]+.114*d[4*i+2];
    const inside=(x,y)=>!p || p.every((q,i)=>cross(q,p[(i+1)%4],[x,y])>.003);
    for(let y=2;y<h-2;y++)for(let x=2;x<w-2;x++) {
      if(!inside(x/(w-1),y/(h-1)))continue;
      const i=y*w+x,g=gray(i),lap=4*g-gray(i-1)-gray(i+1)-gray(i-w)-gray(i+w);
      sum+=lap;sq+=lap*lap;n++;if(g>249)bright++;if(g<150)dark++;
    }
    const sharpness=n?Math.max(0,sq/n-(sum/n)**2):0, saturated=n?bright/n:0;
    if(sharpness<65)flags.push('blur');
    // A mostly white page alone is insufficient to claim glare.
    if((saturated>.015 && saturated<.65) || (saturated>=.65 && dark/Math.max(1,n)<.02))flags.push('glare');
    if(!p)flags.push('detection');
    if(p && p.some(q=>q.some(v=>v<.025||v>.975)))flags.push('cutoff');
    const lengths=p?p.map((q,i)=>distance([q[0]*originalWidth,q[1]*originalHeight],[p[(i+1)%4][0]*originalWidth,p[(i+1)%4][1]*originalHeight])):[originalWidth,originalHeight];
    if(Math.min(...lengths)<900)flags.push('resolution');
    return {flags,sharpness:Math.round(sharpness*100)/100,saturated:Math.round(saturated*10000)/10000};
  }
  function analyze(source, suppliedCorners=undefined) {
    const w=source.videoWidth||source.width,h=source.videoHeight||source.height;
    if(!Number.isFinite(w)||!Number.isFinite(h)||w<2||h<2)throw Error('empty_frame');
    const scale=Math.min(1,320/Math.max(w,h)),canvas=document.createElement('canvas');
    canvas.width=Math.max(2,Math.round(w*scale));canvas.height=Math.max(2,Math.round(h*scale));
    const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(source,0,0,canvas.width,canvas.height);
    const pixels=ctx.getImageData(0,0,canvas.width,canvas.height),corners=suppliedCorners===undefined?detect(pixels):suppliedCorners;
    return {corners,...quality(pixels,corners,w,h)};
  }
  function homography(p) {
    if(!validCorners(p))throw Error('invalid_corners');
    const rows=[];
    [[0,0],[1,0],[1,1],[0,1]].forEach(([u,v],i)=>{const [x,y]=p[i];rows.push([u,v,1,0,0,0,-u*x,-v*x,x],[0,0,0,u,v,1,-u*y,-v*y,y]);});
    for(let col=0;col<8;col++) {
      let pivot=col;for(let r=col+1;r<8;r++)if(Math.abs(rows[r][col])>Math.abs(rows[pivot][col]))pivot=r;
      [rows[col],rows[pivot]]=[rows[pivot],rows[col]];const divisor=rows[col][col];if(Math.abs(divisor)<1e-10)throw Error('invalid_corners');
      for(let j=col;j<9;j++)rows[col][j]/=divisor;
      for(let r=0;r<8;r++)if(r!==col){const f=rows[r][col];for(let j=col;j<9;j++)rows[r][j]-=f*rows[col][j];}
    }
    return rows.map(row=>row[8]);
  }
  const map = (m,u,v) => {const den=m[6]*u+m[7]*v+1;return [(m[0]*u+m[1]*v+m[2])/den,(m[3]*u+m[4]*v+m[5])/den];};
  async function correct(source,p,isCurrent=()=>true) {
    const m=homography(p),w=source.width,h=source.height;
    if(!Number.isInteger(w)||!Number.isInteger(h)||w<2||h<2||w*h>12000000)throw Error('original_size');
    let ow=Math.round(Math.max(distance([p[0][0]*w,p[0][1]*h],[p[1][0]*w,p[1][1]*h]),distance([p[3][0]*w,p[3][1]*h],[p[2][0]*w,p[2][1]*h])));
    let oh=Math.round(Math.max(distance([p[0][0]*w,p[0][1]*h],[p[3][0]*w,p[3][1]*h]),distance([p[1][0]*w,p[1][1]*h],[p[2][0]*w,p[2][1]*h])));
    const scale=Math.min(1,1600/Math.max(ow,oh),Math.sqrt(2000000/(ow*oh)));ow=Math.max(1,Math.floor(ow*scale));oh=Math.max(1,Math.floor(oh*scale));
    const pixels=source.getContext('2d').getImageData(0,0,w,h).data;
    const out=document.createElement('canvas');out.width=ow;out.height=oh;const ctx=out.getContext('2d'),dst=ctx.createImageData(ow,oh);
    for(let y=0;y<oh;y++) {
      if(y%48===0){await new Promise(resolve=>setTimeout(resolve,0));if(!isCurrent())throw Error('capture_canceled');}
      for(let x=0;x<ow;x++) {
        const q=map(m,x/Math.max(1,ow-1),y/Math.max(1,oh-1)),sx=Math.min(w-1,Math.max(0,q[0]*(w-1))),sy=Math.min(h-1,Math.max(0,q[1]*(h-1)));
        const ax=Math.floor(sx),ay=Math.floor(sy),bx=Math.min(w-1,ax+1),by=Math.min(h-1,ay+1),fx=sx-ax,fy=sy-ay,k=4*(y*ow+x);
        for(let c=0;c<3;c++)dst.data[k+c]=(1-fy)*((1-fx)*pixels[4*(ay*w+ax)+c]+fx*pixels[4*(ay*w+bx)+c])+fy*((1-fx)*pixels[4*(by*w+ax)+c]+fx*pixels[4*(by*w+bx)+c]);
        dst.data[k+3]=255;
      }
    }
    ctx.putImageData(dst,0,0);return out;
  }
  return {policy,validCorners,detect,quality,analyze,homography,map,correct};
})();
