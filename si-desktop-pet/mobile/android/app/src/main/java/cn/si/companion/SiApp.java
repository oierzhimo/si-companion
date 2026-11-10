package cn.si.companion;

import android.app.Application;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import org.json.JSONObject;
import org.json.JSONArray;
import java.io.File;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class SiApp extends Application {
    public EventLedger ledger;
    public String startupError=null,message="管理员，我在这里。";
    public boolean quiet=false;
    private JSONObject lines;
    private volatile JSONObject peer;
    private long lastPat=-1000,lastFeed=-20000;
    private final Handler main=new Handler(Looper.getMainLooper());
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final ExecutorService network=Executors.newSingleThreadExecutor();
    private final java.util.concurrent.atomic.AtomicBoolean networkBusy=new java.util.concurrent.atomic.AtomicBoolean();
    private final CopyOnWriteArrayList<Runnable> listeners=new CopyOnWriteArrayList<>();
    private final HashSet<String> active=new HashSet<>();
    private final java.util.Map<String,java.util.List<String>> bags=new java.util.HashMap<>();
    private final java.util.Map<String,String> lastLine=new java.util.HashMap<>();
    private final java.util.Random random=new java.util.Random();
    private final Runnable automatic=new Runnable() {
        public void run() {
            if(!active.isEmpty() && isAutomatic()) { sync();main.postDelayed(this,60000); }
        }
    };

    @Override public void onCreate() {
        super.onCreate();
        try {
            ledger=new EventLedger(new File(getFilesDir(),"ledger.json"));
            try(InputStream input=getAssets().open("lines.json")) {
                java.io.ByteArrayOutputStream output=new java.io.ByteArrayOutputStream();byte[] buffer=new byte[4096];int count;
                while((count=input.read(buffer))!=-1) output.write(buffer,0,count);
                lines=new JSONObject(new String(output.toByteArray(),StandardCharsets.UTF_8));
            }
            File saved=new File(getFilesDir(),"peer.json");
            if(saved.exists()) { peer=new JSONObject(new String(Files.readAllBytes(saved.toPath()),StandardCharsets.UTF_8));PinnedPeer.validate(peer); }
            quiet=getSharedPreferences("local",0).getBoolean("quiet",false);
        } catch(Exception error) { startupError="暂时无法读取本机记录，请保留应用数据后检查："+error.getMessage(); }
    }
    public void addListener(Runnable listener) { listeners.add(listener); }
    public void removeListener(Runnable listener) { listeners.remove(listener); }
    private void changed(String text) { main.post(()->{message=text;for(Runnable listener:listeners) listener.run();}); }
    public void report(String text) { changed(text); }
    public void active(String owner,boolean enabled) {
        if(enabled) active.add(owner);else active.remove(owner);
        main.removeCallbacks(automatic);
        if(!active.isEmpty() && isAutomatic()) main.postDelayed(automatic,60000);
    }
    public boolean isAutomatic() { return getSharedPreferences("local",0).getBoolean("automatic",false); }
    public void automatic(boolean enabled) {
        getSharedPreferences("local",0).edit().putBoolean("automatic",enabled).apply();
        main.removeCallbacks(automatic);if(enabled && !active.isEmpty()) main.postDelayed(automatic,60000);
    }
    public String peerName() { return peer==null?"尚未连接电脑":peer.optString("name","已配对电脑"); }
    private String line(String kind) throws Exception {
        JSONArray options=lines.getJSONArray(kind);
        int count=options.length();
        if(count==0) return "";
        if(count==1) return options.getString(0);
        java.util.List<String> bag=bags.get(kind);
        if(bag==null || bag.isEmpty()) {
            bag=new java.util.ArrayList<>();
            for(int index=0;index<count;index++) bag.add(options.getString(index));
            java.util.Collections.shuffle(bag,random);
            String last=lastLine.get(kind);
            if(last!=null && bag.get(bag.size()-1).equals(last)) {
                int swap=random.nextInt(bag.size()-1);
                java.util.Collections.swap(bag,bag.size()-1,swap);
            }
            bags.put(kind,bag);
        }
        String text=bag.remove(bag.size()-1);
        lastLine.put(kind,text);
        return text;
    }
    public void action(String kind) {
        long now=SystemClock.elapsedRealtime();
        if(kind.equals("pat")) { if(now-lastPat<1000)return;lastPat=now; }
        else if(kind.equals("snack")) { if(now-lastFeed<20000) { changed("慢一点，先让我尝完这一口。 ");return; }lastFeed=now; }
        worker.execute(()->{
            try {
                ledger.add(kind,new JSONObject());
                String text=line(kind.equals("pat")?"pat":"feed");
                ledger.add("journal",new JSONObject().put("text",kind.equals("pat")?"你在手机上摸了摸她的头":"你在手机上送了一份点心"));
                changed(text);
            } catch(Exception error) { changed("这次记录还没保存好："+error.getMessage()); }
        });
    }
    public void contact(String kind) {
        if(kind.equals("quiet")) setQuiet(true);
        worker.execute(()->{
            try { String text=line(kind);ledger.add("journal",new JSONObject().put("text",text));changed(text); }
            catch(Exception error) { changed("这次手记还没保存好："+error.getMessage()); }
        });
    }
    public void setQuiet(boolean value) {
        quiet=value;getSharedPreferences("local",0).edit().putBoolean("quiet",value).apply();
        changed(value?"嗯，我就在边上。你专心。":"我在这里，管理员。");
    }
    public void pair(String invitation) {
        if(invitation.trim().isEmpty()) { changed("先粘贴电脑端的配对邀请。");return; }
        if(!networkBusy.compareAndSet(false,true)) { changed("正在处理上一项连接，请稍候。");return; }
        changed("正在连接你的电脑…");
        network.execute(()->{
            try {
                String model=android.os.Build.MODEL;JSONObject next=PinnedPeer.pair(invitation,ledger.deviceId,model.substring(0,Math.min(60,model.length())));
                File target=new File(getFilesDir(),"peer.json"),temporary=new File(getFilesDir(),"peer.tmp");
                Files.write(temporary.toPath(),JsonCodec.bytes(next));
                Files.move(temporary.toPath(),target.toPath(),StandardCopyOption.REPLACE_EXISTING);
                peer=next;changed("已连接 "+peerName()+"，可以同步记录了。");
            } catch(Exception error) { changed("没有连接成功："+error.getMessage()); }
            finally { networkBusy.set(false); }
        });
    }
    public void sync() {
        if(!networkBusy.compareAndSet(false,true)) { changed("正在同步，请稍候。");return; }
        network.execute(()->{
            try {
                if(peer==null) { changed("先连接你自己的电脑，再同步记录。");return; }
                int count=PinnedPeer.exchange(peer,ledger);
                changed("同步完成，带回 "+count+" 条新记录。");
            } catch(Exception error) { changed("这次没能同步："+error.getMessage()); }
            finally { networkBusy.set(false); }
        });
    }
    public void importBundle(byte[] data) {
        worker.execute(()->{
            try { int count=ledger.importBundle(data);changed("已合并 "+count+" 条记录。"); }
            catch(Exception error) { changed("未能合并："+error.getMessage()); }
        });
    }
    public void forget() {
        worker.execute(()->{
            File saved=new File(getFilesDir(),"peer.json");
            if(saved.exists() && !saved.delete()) { changed("暂时无法移除本机连接。");return; }
            peer=null;changed("已移除本机连接。电脑端可另行撤销这台手机的授权。");
        });
    }
}
