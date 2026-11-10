package cn.si.companion;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.os.Bundle;
import android.os.Build;
import android.provider.Settings;
import android.net.Uri;
import android.text.InputType;
import android.view.View;
import android.view.WindowInsets;
import android.graphics.Color;
import android.widget.*;
import org.json.JSONObject;
import org.json.JSONArray;
import java.nio.charset.StandardCharsets;

public final class MainActivity extends Activity {
    private SiApp app;
    private TextView status,stats,connection;
    private CompanionView character;
    private CheckBox quiet,automatic;
    private EditText invitation;
    private ScrollView page;
    private final String activeOwner="activity-"+System.identityHashCode(this);
    private final Runnable update=()->refresh();
    private int dp(int value) { return Math.round(value*getResources().getDisplayMetrics().density); }
    private TextView text(String value,int size) { TextView v=new TextView(this);v.setText(value);v.setTextSize(size);v.setTextColor(Color.rgb(33,63,55));v.setPadding(0,dp(8),0,dp(8));return v; }
    private Button button(LinearLayout parent,String label,Runnable action) {
        Button result=new Button(this);result.setText(label);result.setAllCaps(false);result.setTextSize(15);result.setMinHeight(dp(48));
        result.setOnClickListener(v->action.run());parent.addView(result,new LinearLayout.LayoutParams(-1,-2));return result;
    }
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);app=(SiApp)getApplication();
        if(app.startupError!=null) { TextView failure=text(app.startupError,17);failure.setPadding(dp(20),dp(50),dp(20),dp(20));setContentView(failure);return; }
        page=new ScrollView(this);page.setBackgroundColor(Color.rgb(238,243,238));
        LinearLayout body=new LinearLayout(this);body.setOrientation(LinearLayout.VERTICAL);body.setPadding(dp(20),dp(12),dp(20),dp(24));page.addView(body);
        body.addView(text("管理员，今天也一起吧。",27));body.addView(text("不必时时说话。需要我时，来摸摸头。",14));
        character=new CompanionView(this);body.addView(character,new LinearLayout.LayoutParams(-1,dp(240)));
        character.setOnClickListener(v->{app.action("pat");character.wave();});
        status=text(app.message,17);body.addView(status);stats=text("",15);body.addView(stats);
        button(body,"摸摸头",()->{app.action("pat");character.wave();});
        button(body,"送一份点心",()->{app.action("snack");character.wave();});
        button(body,"和祀说一句",()->new AlertDialog.Builder(this).setItems(new String[]{"想做点新东西","有点卡住了","想休息一会儿","想听你聊画","先安静陪我"},(d,i)->app.contact(new String[]{"create","stuck","tired","painting","quiet"}[i])).show());
        quiet=new CheckBox(this);quiet.setText("先安静陪我");quiet.setChecked(app.quiet);quiet.setOnCheckedChangeListener((v,value)->{if(value!=app.quiet)app.setQuiet(value);});body.addView(quiet);
        button(body,"查看相伴手记",this::journal);
        button(body,"开启桌面悬浮",this::startOverlay);
        button(body,"停止桌面悬浮",()->stopService(new Intent(this,PetOverlayService.class)));
        body.addView(text("把相伴记录带过来",22));connection=text("",14);body.addView(connection);
        invitation=new EditText(this);invitation.setHint("粘贴电脑端的配对邀请");
        invitation.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_MULTI_LINE|InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        invitation.setMaxLines(4);invitation.setMinLines(2);body.addView(invitation);
        button(body,"连接我的电脑",()->{String value=invitation.getText().toString().trim();app.pair(value);invitation.setText("");});
        button(body,"现在同步",app::sync);
        automatic=new CheckBox(this);automatic.setText("应用打开或悬浮期间，每分钟同步");automatic.setChecked(app.isAutomatic());automatic.setOnCheckedChangeListener((v,value)->app.automatic(value));body.addView(automatic);
        button(body,"移除本机连接",()->new AlertDialog.Builder(this).setMessage("只移除手机保存的连接。电脑端可以另行撤销这台手机的授权。").setNegativeButton("保留",null).setPositiveButton("移除",(d,w)->app.forget()).show());
        button(body,"导出记录包",()->{Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT).setType("application/json").putExtra(Intent.EXTRA_TITLE,"祀-手机记录.json");startActivityForResult(intent,10);});
        button(body,"导入记录包",()->startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("application/json"),11));
        body.addView(text("记录保存在本机。语音与形象重制仍在开发，当前沿用桌面版旧动作素材。",13));
        page.setOnApplyWindowInsetsListener((v,insets)->{
            if(Build.VERSION.SDK_INT>=30) { android.graphics.Insets i=insets.getInsets(WindowInsets.Type.systemBars());v.setPadding(i.left,i.top,i.right,i.bottom); }
            else v.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
            return insets;
        });
        setContentView(page);refresh();handleShare(getIntent());
    }
    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);setIntent(intent);handleShare(intent);
    }
    private void handleShare(Intent intent) {
        if(intent==null || !Intent.ACTION_SEND.equals(intent.getAction()) || invitation==null)return;
        String type=intent.getType();
        if(type!=null && !type.startsWith("text/plain"))return;
        CharSequence extra=intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        String shared=extra==null?"":extra.toString().trim();
        if(shared.isEmpty()) { app.report("没有收到可用的配对邀请，请从电脑端重新分享。");return; }
        if(shared.length()>4096) { app.report("这份邀请太长了，请从电脑端重新复制。");return; }
        if(!usableInvitation(shared)) { app.report("这份邀请暂时无法使用。");return; }
        invitation.setText(shared);
        app.report("已填入配对邀请。请确认电脑名称，然后主动点“连接我的电脑”。");
        revealConnection();
    }
    private boolean usableInvitation(String value) {
        try {
            JSONObject parsed=JsonCodec.parse(value.getBytes(StandardCharsets.UTF_8));
            if(!(parsed.get("transport") instanceof Integer || parsed.get("transport") instanceof Long) || parsed.getLong("transport")!=1)return false;
            if(!(parsed.get("device_id") instanceof String) || !parsed.getString("device_id").matches("[A-Za-z0-9][A-Za-z0-9-]{0,63}"))return false;
            if(!(parsed.get("name") instanceof String) || parsed.getString("name").isEmpty() || parsed.getString("name").length()>60)return false;
            if(!(parsed.get("invitation") instanceof String) || !parsed.getString("invitation").matches("[A-Za-z0-9_-]{32,128}"))return false;
            PinnedPeer.validate(parsed);
            return true;
        } catch(Exception error) { return false; }
    }
    private void revealConnection() {
        if(page==null || connection==null)return;
        page.post(()->page.smoothScrollTo(0,Math.max(0,connection.getTop()-dp(8))));
    }
    private void refresh() {
        if(status==null)return;
        status.setText(app.message);connection.setText(app.peerName());character.quiet(app.quiet);
        if(quiet.isChecked()!=app.quiet)quiet.setChecked(app.quiet);
        try { JSONObject s=app.ledger.summary();stats.setText("亲密度 "+s.getLong("affinity")+"  ·  摸头 "+s.getLong("pats")+" 次  ·  点心 "+s.getLong("snacks")+" 份"); }
        catch(Exception error) { status.setText("暂时无法读取记录："+error.getMessage()); }
    }
    private void journal() {
        try {
            JSONArray rows=app.ledger.journal();StringBuilder content=new StringBuilder();
            for(int i=Math.max(0,rows.length()-35);i<rows.length();i++) { JSONObject row=rows.getJSONObject(i);content.append(row.getString("at")).append('\n').append(row.getString("text")).append("\n\n"); }
            if(content.length()==0)content.append("第一段相伴，从现在开始。");
            new AlertDialog.Builder(this).setTitle("相伴手记").setMessage(content).setPositiveButton("好",null).show();
        } catch(Exception error) { status.setText(error.getMessage()); }
    }
    private void startOverlay() {
        if(!Settings.canDrawOverlays(this)) {
            new AlertDialog.Builder(this).setMessage("桌面陪伴需要你允许显示在其他应用上层。应用内陪伴可以继续使用。").setNegativeButton("留在应用里",null).setPositiveButton("前往设置",(d,w)->startActivityForResult(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,Uri.parse("package:"+getPackageName())),21)).show();return;
        }
        if(Build.VERSION.SDK_INT>=33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS")!=android.content.pm.PackageManager.PERMISSION_GRANTED) { requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"},20);return; }
        try { startForegroundService(new Intent(this,PetOverlayService.class)); }
        catch(RuntimeException error) { status.setText("系统暂未允许悬浮陪伴："+error.getMessage()); }
    }
    @Override protected void onResume() { super.onResume();if(app.startupError==null) { app.addListener(update);app.active(activeOwner,true);refresh(); } }
    @Override protected void onPause() { if(app.startupError==null) { app.removeListener(update);app.active(activeOwner,false); }super.onPause(); }
    @Override protected void onActivityResult(int request,int result,Intent data) {
        super.onActivityResult(request,result,data);
        if(request==21) { if(Settings.canDrawOverlays(this))startOverlay();else status.setText("悬浮权限尚未开启，我们先在应用里待着。 ");return; }
        if(result!=RESULT_OK || data==null || data.getData()==null)return;
        try {
            if(request==10) { try(java.io.OutputStream output=getContentResolver().openOutputStream(data.getData())) { output.write(app.ledger.exportBundle()); }status.setText("记录包已导出。"); }
            if(request==11) {
                java.io.ByteArrayOutputStream output=new java.io.ByteArrayOutputStream();
                try(java.io.InputStream input=getContentResolver().openInputStream(data.getData())) {
                    byte[] buffer=new byte[8192];int count;while((count=input.read(buffer))!=-1) { if(output.size()+count>EventLedger.MAX_BYTES)throw new Exception("记录包超过大小限制");output.write(buffer,0,count); }
                }
                app.importBundle(output.toByteArray());
            }
        } catch(Exception error) { status.setText("未能完成："+error.getMessage()); }
    }
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] grants) {
        super.onRequestPermissionsResult(request,permissions,grants);
        if(request==20) {
            if(grants.length>0 && grants[0]==android.content.pm.PackageManager.PERMISSION_GRANTED)startOverlay();
            else status.setText("通知未允许，先在应用里陪伴。需要时可在系统设置中允许。 ");
        }
    }
}
