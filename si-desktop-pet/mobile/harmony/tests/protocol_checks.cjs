const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const tls = require('node:tls');
const { Ledger, parseWire, pairWith, exchangeWith, utf8Length } = require('../build/protocol/Wire.js');
const { HttpBuffer } = require('../build/protocol/HttpBuffer.js');
const hash = async text => crypto.createHash('sha256').update(text,'utf8').digest('hex');
const decode = bytes => new TextDecoder('utf-8',{fatal:true}).decode(bytes);

class NodeTransport {
  async request(peer, endpoint, body, deviceId='', key='') {
    return new Promise((resolve,reject) => {
      let completed=false, authenticated=false;
      const parser=new HttpBuffer(decode);
      const socket=tls.connect({host:peer.host,port:peer.port,rejectUnauthorized:false,minVersion:'TLSv1.2'});
      socket.setTimeout(12000,()=>socket.destroy(new Error('TLS timeout')));
      socket.on('secureConnect',()=>{
        const fingerprint=crypto.createHash('sha256').update(socket.getPeerCertificate().raw).digest('hex');
        if(fingerprint!==peer.fingerprint) { socket.destroy(new Error('Certificate fingerprint mismatch'));return; }
        authenticated=true;
        let header=`POST ${endpoint} HTTP/1.1\r\nHost: ${peer.host}:${peer.port}\r\nContent-Type: application/json\r\nContent-Length: ${utf8Length(body)}\r\nConnection: close\r\n`;
        if(key) header+=`Authorization: Bearer ${key}\r\nX-Si-Device: ${deviceId}\r\n`;
        socket.write(header+'\r\n'+body,'utf8');
      });
      socket.on('data',chunk=>{
        try {
          assert(authenticated,'Received application data before authentication');
          const reply=parser.push(chunk);
          if(reply!==undefined) { completed=true;resolve(reply);socket.end(); }
        } catch(error) { socket.destroy(error); }
      });
      socket.on('error',reject);
      socket.on('close',()=>{if(!completed)reject(new Error('Incomplete TLS response'));});
    });
  }
}

async function main() {
  const work=process.argv[2];
  const fixture=fs.readFileSync(path.join(work,'fixture.json'),'utf8');
  const metadata=JSON.parse(fs.readFileSync(path.join(work,'expected.json'),'utf8'));
  let saved='',failWrites=false;
  const persist=async text=>{
    if(failWrites)throw new Error('Simulated storage failure');
    fs.writeFileSync(path.join(work,'saved.tmp'),text,'utf8');
    fs.renameSync(path.join(work,'saved.tmp'),path.join(work,'saved.json'));
    saved=text;
  };
  const ledger=new Ledger('harmony-test',hash,persist);
  await ledger.initialize();
  assert.equal(JSON.parse(saved).device_id,'harmony-test');
  assert.equal(await ledger.importBundle(fixture),metadata.events);
  assert.equal(await ledger.importBundle(fixture),0);
  assert.deepEqual(ledger.summary(),metadata.counters);
  assert.deepEqual(ledger.journal().map(row=>row.text),metadata.journal_texts);
  const before=await ledger.exportBundle();
  const broken=JSON.parse(fixture);broken.sha256='0';
  await assert.rejects(ledger.importBundle(JSON.stringify(broken)),/checksum/);
  assert.equal(await ledger.exportBundle(),before);
  const conflict=JSON.parse(fixture);delete conflict.sha256;
  conflict.events.find(event=>event.kind==='journal').payload.text='Conflicting contents';
  // Ask the same production core to derive the canonical event body before changing only the ID contents.
  const { canonicalEvent }=require('../build/protocol/Wire.js');
  const body=`{"events":[${conflict.events.map(canonicalEvent).join(',')}],"protocol":1,"source_device":${JSON.stringify(conflict.source_device)}}`;
  conflict.sha256=await hash(body);
  await assert.rejects(ledger.importBundle(JSON.stringify(conflict)),/Conflicting event ID/);
  assert.equal(await ledger.exportBundle(),before);
  assert.throws(()=>parseWire('{"protocol":1.0}'),/integer token/);
  assert.throws(()=>parseWire('{"protocol":1e0}'),/integer token/);
  assert.throws(()=>parseWire("{'protocol':1}"));
  failWrites=true;
  await assert.rejects(ledger.add('pat',{},'2026-10-27T01:00:00Z'),/storage failure/);
  failWrites=false;
  assert.equal(await ledger.exportBundle(),before,'Failed durable save changed live state');
  await ledger.add('pat',{},'2026-10-27T01:00:00Z');
  await ledger.add('journal',{text:'管理员，平板这边也记下了。🖌️\n</script>\u2028'},'2026-10-27T01:00:01Z');
  await Promise.all([ledger.add('pat',{},'2026-10-27T01:00:02Z'),ledger.add('snack',{},'2026-10-27T01:00:03Z')]);
  const reloaded=new Ledger('harmony-test',hash,persist,saved);
  assert.equal(reloaded.summary().affinity,15);
  fs.writeFileSync(path.join(work,'harmony-export.json'),await reloaded.exportBundle(),'utf8');

  const payload=Buffer.from('{"text":"管理员，墨色🖌️"}','utf8');
  const packet=Buffer.concat([Buffer.from(`HTTP/1.0 200 OK\r\nContent-Length: ${payload.length}\r\n\r\n`),payload]);
  const parser=new HttpBuffer(decode);let reply;
  for(const byte of packet)reply=parser.push(Uint8Array.of(byte));
  assert.deepEqual(reply,{status:200,body:payload.toString('utf8')});
  for(const header of ['Content-Length: 5000001','Content-Length: 1\r\nContent-Length: 1','Transfer-Encoding: chunked']) {
    assert.throws(()=>new HttpBuffer(decode).push(Buffer.from(`HTTP/1.0 200 OK\r\n${header}\r\n\r\n`)));
  }
  assert.throws(()=>new HttpBuffer(decode).push(Buffer.concat([Buffer.from('HTTP/1.0 200 OK\r\nContent-Length: 1\r\n\r\n'),Buffer.from([255])])));

  const invitation=fs.readFileSync(path.join(work,'invitation.txt'),'utf8');
  const transport=new NodeTransport();let networkSaved='';
  const network=new Ledger('harmony-network',hash,async text=>{networkSaved=text;});
  await network.initialize();
  const wrong=JSON.parse(invitation);wrong.fingerprint='0'.repeat(64);
  await assert.rejects(pairWith(JSON.stringify(wrong),network.deviceId,'错误证书检查',transport),/fingerprint/);
  const peer=await pairWith(invitation,network.deviceId,'鸿蒙协议互测',transport);
  await network.add('pat',{},'2026-10-27T02:00:00Z');
  await network.add('journal',{text:'管理员，鸿蒙协议核心已连到电脑。🖌️'},'2026-10-27T02:00:01Z');
  await exchangeWith(peer,network,transport);
  assert.equal(network.summary().affinity,4);
  assert.equal(await exchangeWith(peer,network,transport),0);
  fs.writeFileSync(path.join(work,'network-export.json'),await network.exportBundle(),'utf8');
  assert.equal(new Ledger(network.deviceId,hash,async()=>{},networkSaved).summary().affinity,4);

  // Shared preference projection: same synthetic vectors as Python and Android.
  const vectors=JSON.parse(fs.readFileSync(path.join(__dirname,'../../../tests/fixtures/shared_preferences_vectors.json'),'utf8'));
  assert.equal(vectors.protocol,1);
  const buildBundle=async(events,source='vector-source')=>{
    const body=`{"events":[${events.map(canonicalEvent).join(',')}],"protocol":1,"source_device":${JSON.stringify(source)}}`;
    return `{"events":[${events.map(canonicalEvent).join(',')}],"protocol":1,"sha256":${JSON.stringify(await hash(body))},"source_device":${JSON.stringify(source)}}`;
  };
  const counterOnly=summary=>{
    const {pats,snacks,focus_sessions,affinity}=summary;
    return {pats,snacks,focus_sessions,affinity};
  };
  for(const vector of vectors.vectors) {
    const folder=path.join(work,'prefs-'+vector.name);fs.mkdirSync(folder,{recursive:true});
    const file=path.join(folder,'ledger.json');
    let savedText='',writes=0;
    const persistVector=async text=>{ fs.writeFileSync(file,text,'utf8');savedText=text;writes++; };
    const prefsLedger=new Ledger('harmony-vector',hash,persistVector);
    await prefsLedger.initialize();
    if(vector.events.length) {
      const incoming=await buildBundle(vector.events);
      assert.equal(await prefsLedger.importBundle(incoming),vector.events.length,vector.name+' import count');
      assert.equal(await prefsLedger.importBundle(incoming),0,vector.name+' duplicate import');
    }
    const summaryBefore=prefsLedger.summary();
    assert.deepEqual(counterOnly(summaryBefore),vector.counts,vector.name+' summary counts');
    const exportBefore=await prefsLedger.exportBundle();
    const writesBefore=writes,storedBefore=fs.readFileSync(file);
    assert.deepEqual(prefsLedger.sharedPreferences(),vector.preferences,vector.name+' projection');
    assert.equal(writes,writesBefore,vector.name+' projection wrote storage');
    assert.deepEqual(fs.readFileSync(file),storedBefore,vector.name+' projection changed storage');
    assert.deepEqual(prefsLedger.summary(),summaryBefore,vector.name+' projection changed summary');
    assert.equal(await prefsLedger.exportBundle(),exportBefore,vector.name+' projection changed export');
    const reloadedPrefs=new Ledger('harmony-vector',hash,async()=>{},savedText);
    assert.deepEqual(reloadedPrefs.sharedPreferences(),vector.preferences,vector.name+' reload projection');
    assert.deepEqual(reloadedPrefs.summary(),summaryBefore,vector.name+' reload summary');
    assert.equal(await reloadedPrefs.exportBundle(),exportBefore,vector.name+' reload export');
    if(vector.events.length) {
      const reverse=new Ledger('harmony-vector',hash,async()=>{});
      await reverse.initialize();
      for(const event of vector.events.slice().reverse())
        assert.equal(await reverse.importBundle(await buildBundle([event])),1,vector.name+' reverse import');
      assert.deepEqual(reverse.sharedPreferences(),vector.preferences,vector.name+' reverse projection');
      assert.deepEqual(reverse.summary(),summaryBefore,vector.name+' reverse summary');
      const preference=vector.events.find(event=>event.kind==='pref_set');
      const forged=JSON.parse(JSON.stringify(preference));
      forged.payload.value+='-conflict';
      await assert.rejects(prefsLedger.importBundle(await buildBundle([forged])),/Conflicting event ID/,vector.name+' conflict');
      assert.deepEqual(prefsLedger.sharedPreferences(),vector.preferences,vector.name+' conflict changed projection');
      assert.equal(await prefsLedger.exportBundle(),exportBefore,vector.name+' conflict changed export');
    }
  }
  console.log('Production TypeScript core: golden fixture, microsecond ordering, persistence failure, concurrency, framing and real Node/Python TLS passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
