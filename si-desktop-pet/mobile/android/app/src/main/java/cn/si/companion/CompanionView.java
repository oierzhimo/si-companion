package cn.si.companion;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.os.SystemClock;
import android.view.View;
import org.json.JSONObject;
import org.json.JSONArray;
import java.io.InputStream;
import java.util.HashMap;

/** Mostly stays on a stable frame. Gestures play once; no continuous busy loop. */
public final class CompanionView extends View {
    private final HashMap<String,Bitmap[]> frames=new HashMap<>();
    private final HashMap<String,int[]> durations=new HashMap<>();
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG|Paint.FILTER_BITMAP_FLAG);
    private final java.util.Random random=new java.util.Random();
    private String action="idle";
    private long start=0;
    private boolean quiet=false;
    private final Runnable blink=()->{ if(!quiet && isShown()) animateOnce("idle");else schedule(); };

    public CompanionView(Context context) {
        super(context);setContentDescription("祀，点击可以摸摸头");
        try {
            JSONObject spec;
            try(InputStream input=context.getAssets().open("animations.json")) {
                java.io.ByteArrayOutputStream output=new java.io.ByteArrayOutputStream();byte[] buffer=new byte[4096];int count;
                while((count=input.read(buffer))!=-1) output.write(buffer,0,count);
                spec=new JSONObject(output.toString("UTF-8"));
            }
            for(String state:new String[]{"idle","waving","sitting"}) {
                JSONObject row=spec.getJSONObject(state);JSONArray files=row.getJSONArray("files"),times=row.getJSONArray("durations");
                Bitmap[] images=new Bitmap[files.length()];int[] delays=new int[files.length()];
                for(int i=0;i<images.length;i++) {
                    BitmapFactory.Options options=new BitmapFactory.Options();options.inSampleSize=4;
                    String path=files.getString(i);
                    try(InputStream input=context.getAssets().open(path)) { images[i]=BitmapFactory.decodeStream(input,null,options); }
                    if(images[i]==null) throw new Exception("Missing frame: state="+state+", path=assets/"+path);delays[i]=times.getInt(i);
                }
                frames.put(state,images);durations.put(state,delays);
            }
        } catch(Exception error) {
            android.util.Log.e("SiCompanionAssets","Failed to load companion frames: "+error.getMessage(),error);
            setContentDescription("角色图像暂时不可用");
        }
    }
    public void quiet(boolean value) { if(quiet==value)return;quiet=value;action=value?"sitting":"idle";start=0;invalidate();schedule(); }
    public void wave() { if(!quiet) animateOnce("waving"); }
    private void animateOnce(String state) { removeCallbacks(blink);action=state;start=SystemClock.uptimeMillis();invalidate(); }
    private void schedule() { removeCallbacks(blink);if(isAttachedToWindow()) postDelayed(blink,15000+random.nextInt(15000)); }
    @Override protected void onAttachedToWindow() { super.onAttachedToWindow();schedule(); }
    @Override protected void onDetachedFromWindow() { removeCallbacks(blink);super.onDetachedFromWindow(); }
    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);Bitmap[] images=frames.get(action);if(images==null)return;
        int index=0;
        if(start>0 && isShown()) {
            long elapsed=SystemClock.uptimeMillis()-start;int[] times=durations.get(action);int total=0;
            for(int delay:times) total+=delay;
            if(elapsed<total) {
                long remaining=elapsed;while(index<times.length-1 && remaining>=times[index]) remaining-=times[index++];
                postInvalidateDelayed(32);
            } else { action=quiet?"sitting":"idle";images=frames.get(action);start=0;schedule(); }
        }
        Bitmap bitmap=images[index];float scale=Math.min(getWidth()/(float)bitmap.getWidth(),getHeight()/(float)bitmap.getHeight());
        float width=bitmap.getWidth()*scale,height=bitmap.getHeight()*scale;
        canvas.drawBitmap(bitmap,null,new RectF((getWidth()-width)/2,(getHeight()-height)/2,(getWidth()+width)/2,(getHeight()+height)/2),paint);
    }
}
