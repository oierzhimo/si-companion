package cn.si.companion;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.time.Instant;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.UUID;

/** No Android APIs: this exact code is exercised against the Python peer on a JVM. */
public final class EventLedger {
    public static final int MAX_BYTES=5000000, MAX_EVENTS=10000;
    private final File file;
    public String deviceId;
    private long sequence=0,lamport=0;
    private TreeMap<String,JSONObject> events=new TreeMap<>();
    private static final String[] COUNTERS={"pats","snacks","focus_sessions","affinity"};

    public EventLedger(File file) throws Exception {
        this.file=file;deviceId=UUID.randomUUID().toString();
        if(file.exists()) {
            JSONObject saved=JsonCodec.parse(read(file));
            fields(saved,"protocol","device_id","sequence","lamport","events");
            if(integer(saved.get("protocol"))!=1) fail("Unsupported ledger protocol");
            if(!(saved.get("device_id") instanceof String)) fail("Invalid local device ID");
            deviceId=saved.getString("device_id");device(deviceId);
            sequence=integer(saved.get("sequence"));lamport=integer(saved.get("lamport"));
            JSONArray rows=saved.getJSONArray("events");
            if(rows.length()>MAX_EVENTS) fail("Ledger full");
            for(int i=0;i<rows.length();i++) {
                JSONObject event=rows.getJSONObject(i);validate(event);
                String key=event.getString("event_id");
                if(events.containsKey(key) && !same(events.get(key),event)) fail("Conflicting event ID");
                events.put(key,event);
                if(event.getString("device_id").equals(deviceId) && integer(event.get("sequence"))>sequence)
                    fail("Ledger sequence went backwards");
                lamport=Math.max(lamport,integer(event.get("lamport")));
            }
            baselines(events);
        } else commit(events,0,0);
    }

    private static byte[] read(File file) throws Exception {
        if(file.length()>MAX_BYTES) fail("Ledger too large");
        return Files.readAllBytes(file.toPath());
    }
    private static void fail(String message) { throw new IllegalArgumentException(message); }
    private static long integer(Object value) {
        if(!(value instanceof Integer || value instanceof Long)) fail("Expected a whole non-negative number");
        long number=((Number)value).longValue();
        if(number<0) fail("Negative number");
        return number;
    }
    private static void device(String value) {
        if(!value.matches("[A-Za-z0-9][A-Za-z0-9-]{0,63}")) fail("Invalid device ID");
    }
    private static void fields(JSONObject object,String... expected) {
        TreeSet<String> actual=new TreeSet<>();
        java.util.Iterator<String> iterator=object.keys();
        while(iterator.hasNext()) actual.add(iterator.next());
        TreeSet<String> wanted=new TreeSet<>(java.util.Arrays.asList(expected));
        if(!actual.equals(wanted)) fail("Invalid event fields");
    }
    private static int length(String value) { return value.codePointCount(0,value.length()); }
    private static boolean same(JSONObject a,JSONObject b) throws Exception {
        return JsonCodec.canonical(a).equals(JsonCodec.canonical(b));
    }
    private static void baselines(TreeMap<String,JSONObject> rows) throws Exception {
        int count=0;
        for(JSONObject event:rows.values()) if(event.getString("kind").equals("legacy_snapshot")) count++;
        if(count>1) fail("Conflicting legacy baselines; manual migration required");
    }
    /** Python compares strings by Unicode code point; Java compares UTF-16 units. */
    private static int codePointOrder(String left,String right) {
        int a=0,b=0;
        while(a<left.length() && b<right.length()) {
            int x=left.codePointAt(a),y=right.codePointAt(b);
            if(x!=y) return x<y?-1:1;
            a+=Character.charCount(x);b+=Character.charCount(y);
        }
        return Integer.compare(left.length()-a,right.length()-b);
    }
    /** Tuple order used by Python summary(): (lamport, device_id, event_id, value). */
    private static int preferenceOrder(JSONObject left,JSONObject right) throws Exception {
        int order=Long.compare(left.getLong("lamport"),right.getLong("lamport"));
        if(order!=0) return order;
        order=codePointOrder(left.getString("device_id"),right.getString("device_id"));
        if(order!=0) return order;
        order=codePointOrder(left.getString("event_id"),right.getString("event_id"));
        if(order!=0) return order;
        return codePointOrder(left.getJSONObject("payload").getString("value"),
                              right.getJSONObject("payload").getString("value"));
    }

    public static void validate(JSONObject event) throws Exception {
        fields(event,"protocol","event_id","device_id","sequence","lamport","kind","payload","occurred_at");
        for(String key:new String[]{"event_id","device_id","kind","occurred_at"})
            if(!(event.get(key) instanceof String)) fail("Expected a string: "+key);
        if(integer(event.get("protocol"))!=1) fail("Unsupported event protocol");
        String device=event.getString("device_id");device(device);
        long sequence=integer(event.get("sequence"));
        if(sequence==0 || !event.getString("event_id").equals(device+":"+sequence)) fail("Invalid event ID");
        integer(event.get("lamport"));
        String at=event.getString("occurred_at");
        if(at.length()>40) fail("Invalid timestamp");
        OffsetDateTime.parse(at);
        JSONObject payload=event.getJSONObject("payload");
        switch(event.getString("kind")) {
            case "pat": case "snack": fields(payload);break;
            case "focus_completed":
                fields(payload,"minutes");
                long minutes=integer(payload.get("minutes"));
                if(minutes<1 || minutes>120) fail("Invalid focus duration");break;
            case "journal":
                fields(payload,"text");
                if(!(payload.get("text") instanceof String)) fail("Invalid journal text");
                int size=length(payload.getString("text"));
                if(size<1 || size>500) fail("Invalid journal text");break;
            case "pref_set":
                fields(payload,"key","value");
                if(!(payload.get("key") instanceof String) || !(payload.get("value") instanceof String)) fail("Invalid shared preference");
                String key=payload.getString("key");
                if(!(key.equals("preferred_name") || key.equals("greeting_style")) || length(payload.getString("value"))>80)
                    fail("Invalid shared preference");break;
            case "legacy_snapshot":
                fields(payload,"pats","snacks","focus_sessions","affinity","history");
                for(String counter:COUNTERS) integer(payload.get(counter));
                JSONArray history=payload.getJSONArray("history");
                if(history.length()>100) fail("Invalid legacy history");
                for(int i=0;i<history.length();i++) {
                    JSONObject row=history.getJSONObject(i);fields(row,"at","text");
                    if(!(row.get("at") instanceof String) || !(row.get("text") instanceof String)) fail("Invalid legacy row");
                    if(length(row.getString("at"))>40 || length(row.getString("text"))>500) fail("Invalid legacy row");
                }
                break;
            default: fail("Unknown event kind");
        }
        if(JsonCodec.bytes(event).length>32000) fail("Event too large");
    }

    private void commit(TreeMap<String,JSONObject> next,long nextSequence,long nextLamport) throws Exception {
        JSONObject saved=new JSONObject();
        saved.put("protocol",1).put("device_id",deviceId).put("sequence",nextSequence).put("lamport",nextLamport);
        JSONArray rows=new JSONArray();for(JSONObject event:next.values()) rows.put(event);
        saved.put("events",rows);
        byte[] bytes=JsonCodec.bytes(saved);
        if(bytes.length>MAX_BYTES) fail("Ledger too large");
        File parent=file.getAbsoluteFile().getParentFile();
        if(!parent.isDirectory() && !parent.mkdirs()) fail("Cannot create save folder");
        File temporary=new File(file.getPath()+".tmp");
        try(FileOutputStream output=new FileOutputStream(temporary)) { output.write(bytes);output.getFD().sync(); }
        try { Files.move(temporary.toPath(),file.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING); }
        catch(java.nio.file.AtomicMoveNotSupportedException error) { Files.move(temporary.toPath(),file.toPath(),StandardCopyOption.REPLACE_EXISTING); }
        events=next;sequence=nextSequence;lamport=nextLamport;
    }

    public synchronized void add(String kind,JSONObject payload) throws Exception {
        if(kind.equals("legacy_snapshot") || events.size()>=MAX_EVENTS) fail("Cannot add this event");
        long nextSequence=Math.addExact(sequence,1),nextLamport=Math.addExact(lamport,1);
        JSONObject event=new JSONObject().put("protocol",1).put("device_id",deviceId)
            .put("event_id",deviceId+":"+nextSequence).put("sequence",nextSequence).put("lamport",nextLamport)
            .put("kind",kind).put("payload",new JSONObject(JsonCodec.canonical(payload))).put("occurred_at",Instant.now().truncatedTo(java.time.temporal.ChronoUnit.MICROS).toString());
        validate(event);
        TreeMap<String,JSONObject> next=new TreeMap<>(events);next.put(event.getString("event_id"),event);
        commit(next,nextSequence,nextLamport);
    }

    public synchronized byte[] exportBundle() throws Exception {
        JSONArray rows=new JSONArray();for(JSONObject event:events.values()) rows.put(event);
        JSONObject body=new JSONObject().put("protocol",1).put("source_device",deviceId).put("events",rows);
        body.put("sha256",JsonCodec.sha256(JsonCodec.bytes(body)));
        byte[] result=JsonCodec.bytes(body);
        if(result.length>MAX_BYTES) fail("Bundle too large");
        return result;
    }

    public synchronized int importBundle(byte[] data) throws Exception {
        if(data.length>MAX_BYTES) fail("Bundle too large");
        JSONObject body=JsonCodec.parse(data);
        fields(body,"protocol","source_device","events","sha256");
        if(integer(body.get("protocol"))!=1) fail("Unsupported bundle protocol");
        if(!(body.get("source_device") instanceof String) || !(body.get("sha256") instanceof String)) fail("Invalid bundle source or checksum");
        device(body.getString("source_device"));
        String checksum=body.getString("sha256");body.remove("sha256");
        if(!checksum.equals(JsonCodec.sha256(JsonCodec.bytes(body)))) fail("Bundle checksum mismatch");
        JSONArray rows=body.getJSONArray("events");
        if(rows.length()>MAX_EVENTS) fail("Invalid event batch");
        TreeMap<String,JSONObject> next=new TreeMap<>(events);
        long nextLamport=lamport;
        for(int i=0;i<rows.length();i++) {
            JSONObject event=rows.getJSONObject(i);validate(event);
            String key=event.getString("event_id");
            if(next.containsKey(key) && !same(next.get(key),event)) fail("Conflicting event ID");
            next.put(key,event);nextLamport=Math.max(nextLamport,integer(event.get("lamport")));
        }
        baselines(next);
        if(next.size()>MAX_EVENTS) fail("Ledger full");
        int count=next.size()-events.size();
        if(count>0) commit(next,sequence,Math.addExact(nextLamport,1));
        return count;
    }

    public synchronized JSONObject summary() throws Exception {
        long pats=0,snacks=0,focus=0,affinity=0;
        for(JSONObject event:events.values()) {
            JSONObject p=event.getJSONObject("payload");
            switch(event.getString("kind")) {
                case "legacy_snapshot": pats=Math.addExact(pats,p.getLong("pats"));snacks=Math.addExact(snacks,p.getLong("snacks"));focus=Math.addExact(focus,p.getLong("focus_sessions"));affinity=Math.addExact(affinity,p.getLong("affinity"));break;
                case "pat": pats++;affinity++;break;
                case "snack": snacks++;affinity+=3;break;
                case "focus_completed": focus++;affinity+=5;break;
            }
        }
        return new JSONObject().put("pats",pats).put("snacks",snacks).put("focus_sessions",focus).put("affinity",affinity);
    }

    /**
     * Read-only projection of pref_set events, matching sync_v1.summary() preferences.
     * Empty values are real values. This method never writes storage or uses the network.
     */
    public synchronized JSONObject sharedPreferences() throws Exception {
        JSONObject result=new JSONObject();
        for(String key:new String[]{"preferred_name","greeting_style"}) {
            JSONObject best=null;
            for(JSONObject event:events.values()) {
                if(!event.getString("kind").equals("pref_set")) continue;
                JSONObject payload=event.getJSONObject("payload");
                if(!payload.getString("key").equals(key)) continue;
                if(best==null || preferenceOrder(event,best)>0) best=event;
            }
            if(best!=null) result.put(key,best.getJSONObject("payload").getString("value"));
        }
        return result;
    }

    public synchronized JSONArray journal() throws Exception {
        JSONArray result=new JSONArray();
        for(JSONObject event:events.values()) if(event.getString("kind").equals("legacy_snapshot")) {
            JSONArray rows=event.getJSONObject("payload").getJSONArray("history");
            for(int i=0;i<rows.length();i++) result.put(rows.getJSONObject(i));
        }
        TreeMap<Instant,TreeMap<String,JSONObject>> ordered=new TreeMap<>();
        for(JSONObject event:events.values()) if(event.getString("kind").equals("journal")) {
            String at=event.getString("occurred_at");
            Instant key=OffsetDateTime.parse(at).toInstant().truncatedTo(java.time.temporal.ChronoUnit.MICROS);
            if(!ordered.containsKey(key)) ordered.put(key,new TreeMap<>());
            ordered.get(key).put(event.getString("event_id"),new JSONObject().put("at",at).put("text",event.getJSONObject("payload").getString("text")));
        }
        for(TreeMap<String,JSONObject> instant:ordered.values()) for(JSONObject row:instant.values()) result.put(row);
        return result;
    }
}
