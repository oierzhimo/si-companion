package cn.si.companion;

import android.app.Service;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.KeyguardManager;
import android.os.PowerManager;
import android.os.Handler;
import android.os.IBinder;
import android.os.Build;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.provider.Settings;
import android.graphics.PixelFormat;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;

/** User-started only; no boot receiver, accessibility service, or screen collection. */
public final class PetOverlayService extends Service {
    private WindowManager manager;
    private WindowManager.LayoutParams params;
    private CompanionView pet;
    private SiApp app;
    private final Handler handler=new Handler();
    private float downX,downY;
    private int originalX,originalY;
    private long downAt;
    private boolean moved;
    private final Runnable update=()->{
        if(pet!=null) { pet.quiet(app.quiet);pet.setAlpha(app.quiet?.55f:1f); }
    };
    private final Runnable powerCheck=new Runnable() {
        public void run() {
            if(pet==null)return;
            if(!Settings.canDrawOverlays(PetOverlayService.this)) { stopSelf();return; }
            boolean visible=((PowerManager)getSystemService(POWER_SERVICE)).isInteractive()
                && !((KeyguardManager)getSystemService(KEYGUARD_SERVICE)).isKeyguardLocked();
            pet.setVisibility(visible?View.VISIBLE:View.INVISIBLE);
            handler.postDelayed(this,1000);
        }
    };
    private int dp(int value) { return Math.round(value*getResources().getDisplayMetrics().density); }
    @Override public int onStartCommand(Intent intent,int flags,int startId) {
        if(intent!=null && "stop".equals(intent.getAction())) { stopSelf();return START_NOT_STICKY; }
        if(pet!=null)return START_NOT_STICKY;
        app=(SiApp)getApplication();
        if(app.startupError!=null || !Settings.canDrawOverlays(this)) { stopSelf();return START_NOT_STICKY; }
        NotificationManager notifications=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        notifications.createNotificationChannel(new NotificationChannel("companion","桌面陪伴",NotificationManager.IMPORTANCE_LOW));
        PendingIntent open=PendingIntent.getActivity(this,1,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent stop=PendingIntent.getService(this,2,new Intent(this,PetOverlayService.class).setAction("stop"),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        Notification note=new Notification.Builder(this,"companion").setSmallIcon(cn.si.companion.R.drawable.ic_notification)
            .setContentTitle("祀在这里，管理员").setContentText("点击打开；可随时停止悬浮陪伴。")
            .setContentIntent(open).setOngoing(true).addAction(new Notification.Action.Builder(null,"停止陪伴",stop).build()).build();
        try {
            if(Build.VERSION.SDK_INT>=34) startForeground(7,note,ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            else startForeground(7,note);
            manager=(WindowManager)getSystemService(WINDOW_SERVICE);
            pet=new CompanionView(this);
            params=new WindowManager.LayoutParams(dp(110),dp(120),WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE|WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,PixelFormat.TRANSLUCENT);
            params.gravity=Gravity.TOP|Gravity.LEFT;params.x=Math.max(0,width()-params.width);params.y=dp(180);
            clamp();pet.setOnTouchListener((view,event)->touch(event));manager.addView(pet,params);
            app.addListener(update);app.active("overlay",true);update.run();handler.post(powerCheck);
            app.report("我在屏幕边上。拖动可以换个位置，长按回到应用。");
        } catch(RuntimeException error) {
            android.util.Log.e("SiOverlay","Unable to start visible overlay",error);
            app.report("系统暂未允许悬浮陪伴，请回到应用检查悬浮与通知权限。");stopSelf();
        }
        return START_NOT_STICKY;
    }
    private int width() { return getResources().getDisplayMetrics().widthPixels; }
    private int height() { return getResources().getDisplayMetrics().heightPixels; }
    private void clamp() { params.x=Math.max(0,Math.min(params.x,width()-params.width));params.y=Math.max(dp(24),Math.min(params.y,height()-params.height-dp(48))); }
    private boolean touch(MotionEvent event) {
        if(event.getAction()==MotionEvent.ACTION_DOWN) {
            downX=event.getRawX();downY=event.getRawY();originalX=params.x;originalY=params.y;downAt=android.os.SystemClock.uptimeMillis();moved=false;return true;
        }
        if(event.getAction()==MotionEvent.ACTION_MOVE) {
            float x=event.getRawX()-downX,y=event.getRawY()-downY;
            if(Math.abs(x)+Math.abs(y)>dp(8))moved=true;
            if(moved) { params.x=originalX+(int)x;params.y=originalY+(int)y;clamp();safeMove(); }return true;
        }
        if(event.getAction()==MotionEvent.ACTION_UP) {
            if(moved) {
                int from=params.x,to=(params.x+params.width/2<width()/2)?0:Math.max(0,width()-params.width);
                android.animation.ValueAnimator animator=android.animation.ValueAnimator.ofInt(from,to);animator.setDuration(160);
                animator.addUpdateListener(a->{if(pet!=null) { params.x=(Integer)a.getAnimatedValue();clamp();safeMove(); }});animator.start();
            } else if(android.os.SystemClock.uptimeMillis()-downAt>600) {
                startActivity(new Intent(this,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            } else { app.action("pat");pet.wave(); }
            return true;
        }
        return true;
    }
    private void safeMove() { try { if(pet!=null)manager.updateViewLayout(pet,params); }catch(RuntimeException error) { stopSelf(); } }
    @Override public void onConfigurationChanged(android.content.res.Configuration configuration) { super.onConfigurationChanged(configuration);if(params!=null) { clamp();safeMove(); } }
    @Override public void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        if(app!=null) { app.removeListener(update);app.active("overlay",false); }
        if(pet!=null) { try { manager.removeView(pet); }catch(RuntimeException ignored) {}pet=null; }
        stopForeground(STOP_FOREGROUND_REMOVE);super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
