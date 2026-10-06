// The dialog owns all streams, timers, canvases, object URLs and stale callbacks.
function openCapture(docId, label, opener) {
  const video=el('video',{autoplay:true,muted:true,playsinline:true,'aria-label':label});video.muted=true;
  const guide=el('div',{class:'capture-guide','aria-hidden':'true'},[1,2,3,4].map(()=>el('span',{})));
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('class','capture-outline');svg.setAttribute('aria-hidden','true');
  const polygon=document.createElementNS(svg.namespaceURI,'polygon');svg.append(polygon);
  const view=el('div',{class:'capture-view'},video,guide,svg),status=el('p',{role:'status','aria-live':'polite'},cw('starting'));
  const controls=el('div',{class:'capture-controls'}),editor=el('div',{class:'capture-corners',hidden:true});
  const warnings=el('div',{class:'capture-warning',hidden:true}),ack=el('input',{type:'checkbox'});
  const acknowledge=el('label',{},ack,cw('override'));
  const body=el('div',{id:'document-capture'},el('p',{},cw('position')),view,status,editor,warnings,controls,
    el('div',{class:'upl'},fileUpload(docId,label,closeLayer)));
  let stream=null,closed=false,generation=0,timer=null,originalCanvas=null,originalBlob=null,correctedBlob=null,selection='original',corners=null,result=null,originalResult=null,selectedCorners=null;
  const urls=new Set();
  const url=blob=>{const u=URL.createObjectURL(blob);urls.add(u);return u;};
  const revoke=()=>{urls.forEach(u=>URL.revokeObjectURL(u));urls.clear();};
  const stop=()=>{clearTimeout(timer);timer=null;if(stream){stream.getTracks().forEach(t=>t.stop());stream=null;}video.srcObject=null;};
  const cleanup=()=>{closed=true;generation++;stop();revoke();originalCanvas=null;originalBlob=null;correctedBlob=null;result=null;corners=null;activeCapture=null;document.removeEventListener('keydown',trapFocus);(opener?.isConnected?opener:$('helpbtn'))?.focus();};
  const trapFocus=event=>{
    if(event.key!=='Tab')return;const panel=body.closest('.panel');
    const list=[...panel.querySelectorAll('button:not([disabled]),input:not([disabled]),a[href]')].filter(n=>n.getClientRects().length),first=list[0],last=list.at(-1);
    if(event.shiftKey&&(document.activeElement===first||!panel.contains(document.activeElement))){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&(document.activeElement===last||!panel.contains(document.activeElement))){event.preventDefault();first?.focus();}
  };
  const outline=(p,w,h,editable=false)=>{
    svg.setAttribute('viewBox',`0 0 ${w} ${h}`);polygon.setAttribute('points',p?p.map(q=>`${q[0]*w},${q[1]*h}`).join(' '):'');
    svg.querySelectorAll('circle').forEach(n=>n.remove());
    if(editable&&p)p.forEach((q,i)=>{
      const handle=document.createElementNS(svg.namespaceURI,'circle');handle.setAttribute('cx',q[0]*w);handle.setAttribute('cy',q[1]*h);handle.setAttribute('r',Math.max(w,h)*.025);
      handle.addEventListener('pointerdown',event=>{
        event.preventDefault();handle.setPointerCapture(event.pointerId);
        const dragCanvas=originalCanvas;let dragGeneration=generation;
        const move=e=>{if(closed||!corners||originalCanvas!==dragCanvas||generation!==dragGeneration)return;dragGeneration=++generation;const point=new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.getScreenCTM().inverse());corners[i]=[Math.max(0,Math.min(1,point.x/w)),Math.max(0,Math.min(1,point.y/h))];updateCorners();};
        const end=()=>{handle.removeEventListener('pointermove',move);handle.removeEventListener('pointerup',end);handle.removeEventListener('pointercancel',end);if(!closed&&corners&&originalCanvas===dragCanvas&&generation===dragGeneration)updateCorners(true);};
        handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',end);handle.addEventListener('pointercancel',end);
      });svg.append(handle);
    });
  };
  let cornerInputs=[],correctButton=null,useButton=null;
  const updateCorners=(redraw=false)=>{
    cornerInputs.forEach((pair,i)=>pair.forEach((input,j)=>input.value=Math.round(corners[i][j]*1000)/10));
    polygon.setAttribute('points',corners.map(q=>`${q[0]*originalCanvas.width},${q[1]*originalCanvas.height}`).join(' '));
    if(redraw)outline(corners,originalCanvas.width,originalCanvas.height,true);
    if(correctButton)correctButton.disabled=!CaptureVision.validCorners(corners);
    if(useButton)useButton.disabled=!!result.flags.length&&!ack.checked;
  };
  const showWarnings=()=>{warnings.hidden=!result.flags.length;warnings.replaceChildren(...result.flags.map(f=>el('p',{},cw(f))),acknowledge);};
  const showOriginal=()=>{revoke();view.replaceChildren(el('img',{src:url(originalBlob),alt:`${t('photo')}: ${label}`}),svg);selection='original';correctedBlob=null;selectedCorners=null;result=originalResult;showWarnings();editor.hidden=false;outline(corners,originalCanvas.width,originalCanvas.height,true);};
  const correction=async()=>{
    const version=++generation;correctButton.disabled=true;useButton.disabled=true;status.textContent=cw('correcting');
    const snapshot=corners.map(q=>q.slice());
    try {
      const adjusted=await CaptureVision.correct(originalCanvas,snapshot,()=>!closed&&generation===version);
      if(closed||generation!==version){adjusted.width=0;adjusted.height=0;return;}
      const selectedQuality=CaptureVision.analyze(adjusted,[[0,0],[1,0],[1,1],[0,1]]);
      const qualityFlags=[...new Set([...originalResult.flags,...selectedQuality.flags.filter(f=>['blur','glare','resolution'].includes(f))])];
      const blob=await new Promise(resolve=>adjusted.toBlob(resolve,'image/jpeg',.92));
      adjusted.width=0;adjusted.height=0;if(closed||generation!==version)return;
      if(!blob)throw Error('capture');correctedBlob=blob;selection='corrected';selectedCorners=snapshot;result={...selectedQuality,flags:qualityFlags};ack.checked=false;showWarnings();revoke();
      view.replaceChildren(el('img',{src:url(blob),alt:cw('correctedPreview')}));editor.hidden=true;status.textContent=cw('checkCrop');
    } catch(error){if(!closed&&generation===version){showOriginal();status.textContent=cw('failed');}}
    finally{if(!closed&&generation===version){correctButton.disabled=!CaptureVision.validCorners(corners);useButton.disabled=!!result.flags.length&&!ack.checked;}}
  };
  const capture=async()=>{
    const version=++generation;clearTimeout(timer);timer=null;shutter.disabled=true;
    const canvas=document.createElement('canvas');canvas.width=video.videoWidth;canvas.height=video.videoHeight;
    if(!canvas.width||!canvas.height||canvas.width*canvas.height>12000000){status.textContent=cw('failed');shutter.disabled=false;return;}
    try{
      canvas.getContext('2d').drawImage(video,0,0);stop();
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.92));
      if(closed||version!==generation){canvas.width=0;return;}
      if(!blob||blob.size>15*1024*1024)throw Error('capture');
      originalCanvas=canvas;originalBlob=blob;
      try{result=CaptureVision.analyze(canvas);}catch(error){result={corners:null,flags:['detection'],sharpness:0,saturated:0};}
      originalResult=result;corners=result.corners||[[.05,.05],[.95,.05],[.95,.95],[.05,.95]];
      ack.checked=false;warnings.hidden=!result.flags.length;warnings.replaceChildren(...result.flags.map(f=>el('p',{},cw(f))),acknowledge);
      const retake=el('button',{class:'btn ghost',type:'button',onclick:start},cw('retake'));
      correctButton=el('button',{class:'btn soft',type:'button',onclick:correction},cw('correct'));
      const originalButton=el('button',{class:'btn ghost',type:'button',onclick:()=>{generation++;showOriginal();status.textContent=cw('preview');updateCorners();}},cw('original'));
      useButton=el('button',{class:'btn',type:'button',onclick:()=>{
        if(result.flags.length&&!ack.checked)return;
        const file=new File([originalBlob],'document-photo.jpg',{type:'image/jpeg'}),attachment=selection==='corrected'?new File([correctedBlob],'document-corrected.jpg',{type:'image/jpeg'}):null;
        const metadata={policy:CaptureVision.policy,source_dimensions:[canvas.width,canvas.height],corners:selection==='corrected'?selectedCorners:originalResult.corners,selection,flags:result.flags,override:ack.checked,sharpness:result.sharpness,saturated:result.saturated};
        closeLayer();sendPhoto(docId,{files:[file],capture:metadata,derivative:attachment},null);
      }},icon('upload'),cw('use'));
      ack.onchange=()=>updateCorners();
      cornerInputs=corners.map((q,i)=>q.map((v,j)=>el('input',{type:'number',min:0,max:100,step:.1,value:Math.round(v*1000)/10,'aria-label':`${cw('corner')} ${i+1} ${j===0?cw('horizontal'):cw('vertical')} (%)`,oninput:event=>{
        generation++;if(selection==='corrected')showOriginal();corners[i][j]=Math.max(0,Math.min(1,Number(event.target.value)/100));updateCorners(true);
      }})));
      editor.replaceChildren(...cornerInputs.map((pair,i)=>el('label',{},`${cw('corner')} ${i+1}: `,...pair)));
      controls.replaceChildren(retake,correctButton,originalButton,useButton);showOriginal();updateCorners();status.textContent=cw('preview');retake.focus({preventScroll:true});body.closest('.panel').scrollTop=0;
    }catch(error){if(!closed&&version===generation){stop();status.textContent=cw('failed');controls.replaceChildren(el('button',{class:'btn',type:'button',onclick:start},cw('retake')));}}
  };
  const shutter=el('button',{class:'btn',type:'button',disabled:true,onclick:capture},icon('camera'),cw('shutter'));
  const analyzeLive=version=>{
    if(closed||version!==generation||!stream)return;
    try{const r=CaptureVision.analyze(video);outline(r.corners,video.videoWidth,video.videoHeight);const message=r.corners?cw('detected'):cw('detection');if(status.textContent!==message)status.textContent=message;}catch(error){polygon.setAttribute('points','');}
    timer=setTimeout(()=>analyzeLive(version),650);
  };
  const start=async()=>{
    const version=++generation;stop();revoke();if(originalCanvas){originalCanvas.width=0;originalCanvas.height=0;}originalCanvas=null;originalBlob=null;correctedBlob=null;result=null;
    editor.hidden=true;warnings.hidden=true;view.replaceChildren(video,guide,svg);polygon.setAttribute('points','');svg.querySelectorAll('circle').forEach(n=>n.remove());controls.replaceChildren(shutter);shutter.disabled=true;status.textContent=cw('starting');
    if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){status.textContent=cw('unavailable');return;}
    try{
      const acquired=await navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'environment'},width:{ideal:1920,max:3840},height:{ideal:1080,max:2160}}});
      if(closed||version!==generation){acquired.getTracks().forEach(t=>t.stop());return;}
      stream=acquired;video.srcObject=stream;await video.play();if(closed||version!==generation)return;
      status.textContent=cw('ready');shutter.disabled=false;shutter.focus();analyzeLive(version);
    }catch(error){if(!closed&&version===generation){stop();status.textContent=cw('unavailable');}}
  };
  openLayer(`${t('photo')}: ${label}`,body,cleanup);activeCapture=cleanup;document.addEventListener('keydown',trapFocus);start();
}
