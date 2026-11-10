package cn.si.companion;
import java.io.File;
import java.nio.file.Files;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

public final class NetworkChecks {
    public static void main(String[] args) throws Exception {
        String invitation=new String(Files.readAllBytes(new File(args[0]).toPath()),StandardCharsets.UTF_8);
        EventLedger ledger=new EventLedger(new File(args[1]));
        JSONObject peer=PinnedPeer.pair(invitation,ledger.deviceId,"Android Java protocol check");
        ledger.add("pat",new JSONObject());
        ledger.add("journal",new JSONObject().put("text","管理员，手机这边也记下了。🖌️"));
        PinnedPeer.exchange(peer,ledger);
        if(ledger.summary().getLong("affinity")!=4) throw new AssertionError("TLS roundtrip summary differs");
        if(PinnedPeer.exchange(peer,ledger)!=0) throw new AssertionError("Retry duplicated events");
        Files.write(new File(args[2]).toPath(),ledger.exportBundle());
        JSONObject wrong=new JSONObject(invitation).put("fingerprint",new String(new char[64]).replace('\0','0'));
        try { PinnedPeer.pair(wrong.toString(),"different-device","Wrong fingerprint");throw new AssertionError("Bad fingerprint accepted"); }
        catch(javax.net.ssl.SSLException expected) {}
        System.out.println("Actual JVM-to-Python pinned TLS pairing, exchange, retry and fingerprint rejection passed.");
    }
}
