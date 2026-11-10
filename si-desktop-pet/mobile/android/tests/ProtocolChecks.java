package cn.si.companion;

import org.json.JSONArray;
import org.json.JSONObject;
import java.nio.file.Files;
import java.io.File;
import java.nio.charset.StandardCharsets;

public final class ProtocolChecks {
    static void check(boolean value,String message) { if(!value) throw new AssertionError(message); }
    private static final String VECTORS="tests/fixtures/shared_preferences_vectors.json";

    private static File repositoryRoot(File start) {
        for(File current=start.getAbsoluteFile();current!=null;current=current.getParentFile())
            if(new File(current,VECTORS).isFile()) return current;
        for(File current=new File(".").getAbsoluteFile();current!=null;current=current.getParentFile())
            if(new File(current,VECTORS).isFile()) return current;
        throw new AssertionError("Shared preference vectors not found");
    }
    private static JSONObject bundle(JSONArray events) throws Exception {
        JSONObject body=new JSONObject().put("protocol",1).put("source_device","vector-source").put("events",events);
        body.put("sha256",JsonCodec.sha256(JsonCodec.bytes(body)));
        return body;
    }
    private static JSONObject counts(JSONObject summary) throws Exception {
        return new JSONObject().put("pats",summary.getLong("pats")).put("snacks",summary.getLong("snacks"))
            .put("focus_sessions",summary.getLong("focus_sessions")).put("affinity",summary.getLong("affinity"));
    }
    private static void sameJson(Object actual,Object expected,String message) throws Exception {
        if(!JsonCodec.canonical(actual).equals(JsonCodec.canonical(expected))) throw new AssertionError(message);
    }
    private static void sharedPreferencesChecks(File directory) throws Exception {
        JSONObject fixture=JsonCodec.parse(Files.readAllBytes(new File(repositoryRoot(directory),VECTORS).toPath()));
        check(fixture.getInt("protocol")==1,"Shared preference vector protocol changed");
        JSONArray vectors=fixture.getJSONArray("vectors");
        for(int index=0;index<vectors.length();index++) {
            JSONObject vector=vectors.getJSONObject(index);
            String name=vector.getString("name");
            JSONArray rows=vector.getJSONArray("events");
            JSONObject expected=vector.getJSONObject("preferences"),expectedCounts=vector.getJSONObject("counts");
            File folder=new File(directory,"prefs-"+name);folder.mkdirs();
            File saved=new File(folder,"ledger.json");
            EventLedger ledger=new EventLedger(saved);
            if(rows.length()>0) {
                byte[] incoming=JsonCodec.bytes(bundle(rows));
                check(ledger.importBundle(incoming)==rows.length(),"Wrong vector import count: "+name);
                check(ledger.importBundle(incoming)==0,"Duplicate vector import counted: "+name);
            }
            JSONObject summary=ledger.summary();
            sameJson(counts(summary),expectedCounts,"Vector counts differ: "+name);
            byte[] beforeExport=ledger.exportBundle(),beforeFile=Files.readAllBytes(saved.toPath());
            sameJson(ledger.sharedPreferences(),expected,"Preference projection differs: "+name);
            check(java.util.Arrays.equals(beforeExport,ledger.exportBundle()),"Projection changed JSON: "+name);
            check(java.util.Arrays.equals(beforeFile,Files.readAllBytes(saved.toPath())),"Projection wrote storage: "+name);
            sameJson(ledger.summary(),summary,"Projection changed summary: "+name);
            EventLedger reloaded=new EventLedger(saved);
            sameJson(reloaded.sharedPreferences(),expected,"Reloaded projection differs: "+name);
            sameJson(reloaded.summary(),summary,"Reloaded summary differs: "+name);
            check(java.util.Arrays.equals(beforeExport,reloaded.exportBundle()),"Reload changed JSON: "+name);
            if(rows.length()==0) sameJson(ledger.sharedPreferences(),new JSONObject(),"Empty event map: "+name);
            if(rows.length()>0) {
                EventLedger reversed=new EventLedger(new File(folder,"reversed.json"));
                for(int row=rows.length()-1;row>=0;row--) {
                    JSONArray one=new JSONArray().put(rows.getJSONObject(row));
                    check(reversed.importBundle(JsonCodec.bytes(bundle(one)))==1,"Reverse import failed: "+name);
                }
                sameJson(reversed.summary(),summary,"Import order changed summary: "+name);
                sameJson(reversed.sharedPreferences(),expected,"Import order changed projection: "+name);
                JSONObject forged=null;
                for(int row=0;row<rows.length();row++) {
                    JSONObject event=rows.getJSONObject(row);
                    if(event.getString("kind").equals("pref_set")) {
                        forged=new JSONObject(JsonCodec.canonical(event));break;
                    }
                }
                if(forged!=null) {
                    forged.getJSONObject("payload").put("value",forged.getJSONObject("payload").getString("value")+"-conflict");
                    JSONArray one=new JSONArray().put(forged);
                    try {
                        ledger.importBundle(JsonCodec.bytes(bundle(one)));
                        throw new AssertionError("Conflicting preference ID accepted: "+name);
                    } catch(IllegalArgumentException expectedError) {}
                    check(java.util.Arrays.equals(beforeExport,ledger.exportBundle()),"Conflict overwrote preferences: "+name);
                    sameJson(ledger.sharedPreferences(),expected,"Conflict changed projection: "+name);
                }
            }
        }
    }
    public static void main(String[] args) throws Exception {
        File directory=new File(args[0]);directory.mkdirs();
        EventLedger ledger=new EventLedger(new File(directory,"android-ledger.json"));
        String identity=ledger.deviceId;
        check(new EventLedger(new File(directory,"android-ledger.json")).deviceId.equals(identity),"Empty identity was not durable");
        byte[] fixture=Files.readAllBytes(new File(args[1]).toPath());
        JSONObject original=new JSONObject(new String(fixture,StandardCharsets.UTF_8));
        int imported=ledger.importBundle(fixture);check(imported==4,"Wrong fixture import count");
        check(ledger.importBundle(fixture)==0,"Duplicate import counted twice");
        check(ledger.summary().getLong("affinity")==10,"Python fixture summary differs");
        String quoted=JsonCodec.canonical(new JSONObject().put("text","管理员，墨色🖌️\n</script>\u2028"));
        Files.write(new File(directory,"java-canonical.json").toPath(),quoted.getBytes(StandardCharsets.UTF_8));
        String before=new String(ledger.exportBundle(),StandardCharsets.UTF_8);
        original.put("sha256","0");
        try { ledger.importBundle(JsonCodec.bytes(original));throw new AssertionError("Tampered digest accepted"); }
        catch(IllegalArgumentException expected) {}
        check(before.equals(new String(ledger.exportBundle(),StandardCharsets.UTF_8)),"Failed merge mutated ledger");
        JSONObject conflict=JsonCodec.parse(fixture);conflict.remove("sha256");
        conflict.getJSONArray("events").getJSONObject(3).put("occurred_at","2026-10-27T01:11:00Z");
        conflict.put("sha256",JsonCodec.sha256(JsonCodec.bytes(conflict)));
        try { ledger.importBundle(JsonCodec.bytes(conflict));throw new AssertionError("Conflicting ID accepted"); }
        catch(IllegalArgumentException expected) {}
        check(before.equals(new String(ledger.exportBundle(),StandardCharsets.UTF_8)),"Conflict caused a partial merge");
        try { JsonCodec.parse("{'protocol':1}".getBytes(StandardCharsets.UTF_8));throw new AssertionError("Non-standard JSON accepted"); }
        catch(IllegalArgumentException expected) {}
        try { JsonCodec.parse(new byte[]{(byte)0xff});throw new AssertionError("Malformed UTF-8 accepted"); }
        catch(java.nio.charset.CharacterCodingException expected) {}
        ledger.add("pat",new JSONObject());
        ledger.add("journal",new JSONObject().put("text","管理员，我也到这边来了。🖌️"));
        Files.write(new File(directory,"android-export.json").toPath(),ledger.exportBundle());
        check(new EventLedger(new File(directory,"android-ledger.json")).summary().getLong("affinity")==11,"Reload lost events");
        sharedPreferencesChecks(directory);
        System.out.println("Cross-language fixture, Unicode canonical JSON, duplicate/tamper rejection and persistence passed.");
    }
}
