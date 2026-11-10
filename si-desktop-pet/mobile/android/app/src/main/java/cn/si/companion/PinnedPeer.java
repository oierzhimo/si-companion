package cn.si.companion;

import org.json.JSONObject;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.X509TrustManager;
import javax.net.ssl.TrustManager;
import java.security.cert.X509Certificate;
import java.security.cert.CertificateException;
import java.security.MessageDigest;
import java.net.URL;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/** A pinned certificate must validate during the TLS handshake, before sending credentials. */
public final class PinnedPeer {
    public static void validate(JSONObject peer) throws Exception {
        if(!(peer.get("host") instanceof String) || !(peer.get("fingerprint") instanceof String)
            || !(peer.get("port") instanceof Integer || peer.get("port") instanceof Long)) throw new Exception("配对信息类型不正确");
        String host=peer.getString("host");
        if(!host.matches("[0-9]{1,3}(\\.[0-9]{1,3}){3}")) throw new Exception("仅支持局域网 IPv4 地址");
        String[] parts=host.split("\\.");int[] ip=new int[4];
        for(int i=0;i<4;i++) { ip[i]=Integer.parseInt(parts[i]);if(ip[i]>255) throw new Exception("地址不正确"); }
        if(!host.equals(ip[0]+"."+ip[1]+"."+ip[2]+"."+ip[3])) throw new Exception("地址格式不正确");
        if(!(ip[0]==10 || ip[0]==127 || (ip[0]==172 && ip[1]>=16 && ip[1]<=31) || (ip[0]==192 && ip[1]==168)))
            throw new Exception("仅支持本机与局域网地址");
        if(!peer.getString("fingerprint").matches("[0-9a-f]{64}") || peer.getLong("port")<1 || peer.getLong("port")>65535)
            throw new Exception("配对信息不正确");
    }

    private static boolean matches(X509Certificate cert,String fingerprint) throws Exception {
        return MessageDigest.isEqual(JsonCodec.sha256(cert.getEncoded()).getBytes(StandardCharsets.US_ASCII),
                                     fingerprint.getBytes(StandardCharsets.US_ASCII));
    }

    private static byte[] request(JSONObject peer,String endpoint,byte[] body,String device,String key) throws Exception {
        validate(peer);
        final String fingerprint=peer.getString("fingerprint");
        X509TrustManager trust=new X509TrustManager() {
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
            public void checkClientTrusted(X509Certificate[] chain,String auth) throws CertificateException { throw new CertificateException("Client authentication unsupported"); }
            public void checkServerTrusted(X509Certificate[] chain,String auth) throws CertificateException {
                try { if(chain.length==0 || !matches(chain[0],fingerprint)) throw new CertificateException("对方证书已变化，请重新配对"); }
                catch(CertificateException error) { throw error; }
                catch(Exception error) { throw new CertificateException(error); }
            }
        };
        SSLContext context=SSLContext.getInstance("TLS");context.init(null,new TrustManager[]{trust},null);
        URL url=new URL("https",peer.getString("host"),peer.getInt("port"),endpoint);
        HttpsURLConnection connection=(HttpsURLConnection)url.openConnection();
        connection.setSSLSocketFactory(context.getSocketFactory());
        connection.setHostnameVerifier((host,session)-> {
            try { return host.equals(peer.getString("host")) && matches((X509Certificate)session.getPeerCertificates()[0],fingerprint); }
            catch(Exception error) { return false; }
        });
        connection.setConnectTimeout(12000);connection.setReadTimeout(12000);
        connection.setInstanceFollowRedirects(false);connection.setRequestMethod("POST");
        connection.setRequestProperty("Content-Type","application/json");
        if(key!=null) { connection.setRequestProperty("Authorization","Bearer "+key);connection.setRequestProperty("X-Si-Device",device); }
        connection.setDoOutput(true);connection.setFixedLengthStreamingMode(body.length);
        try {
            try(java.io.OutputStream output=connection.getOutputStream()) { output.write(body); }
            int status=connection.getResponseCode();
            InputStream stream=status==200?connection.getInputStream():connection.getErrorStream();
            if(stream==null) throw new Exception("对方未返回有效响应");
            byte[] response;
            try(InputStream input=stream;ByteArrayOutputStream output=new ByteArrayOutputStream()) {
                byte[] buffer=new byte[8192];int size;
                while((size=input.read(buffer))!=-1) {
                    if(output.size()+size>EventLedger.MAX_BYTES) throw new Exception("对方数据超过大小限制");
                    output.write(buffer,0,size);
                }
                response=output.toByteArray();
            }
            if(status!=200) throw new Exception(new JSONObject(new String(response,StandardCharsets.UTF_8)).optString("error","同步未完成"));
            return response;
        } finally { connection.disconnect(); }
    }

    public static JSONObject pair(String text,String device,String name) throws Exception {
        if(text.length()>4096) throw new Exception("邀请过长");
        JSONObject invitation=JsonCodec.parse(text.getBytes(StandardCharsets.UTF_8));validate(invitation);
        if(!(invitation.get("transport") instanceof Integer || invitation.get("transport") instanceof Long) || invitation.getLong("transport")!=1) throw new Exception("不支持的配对版本");
        if(!(invitation.get("device_id") instanceof String) || !invitation.getString("device_id").matches("[A-Za-z0-9][A-Za-z0-9-]{0,63}")
            || !(invitation.get("name") instanceof String) || !(invitation.get("invitation") instanceof String)
            || !invitation.getString("invitation").matches("[A-Za-z0-9_-]{32,128}")) throw new Exception("配对邀请不正确");
        JSONObject body=new JSONObject().put("invitation",invitation.getString("invitation")).put("device_id",device).put("name",name);
        JSONObject answer=JsonCodec.parse(request(invitation,"/v1/pair",JsonCodec.bytes(body),null,null));
        if(!answer.getString("device_id").equals(invitation.getString("device_id")) || !answer.getString("key").matches("[A-Za-z0-9_-]{32,128}"))
            throw new Exception("对方配对响应不正确");
        invitation.remove("invitation");invitation.remove("transport");
        invitation.put("key",answer.getString("key"));
        return invitation;
    }

    public static int exchange(JSONObject peer,EventLedger ledger) throws Exception {
        byte[] response=request(peer,"/v1/exchange",ledger.exportBundle(),ledger.deviceId,peer.getString("key"));
        if(!JsonCodec.parse(response).getString("source_device").equals(peer.getString("device_id")))
            throw new Exception("对方记录来源与配对信息不一致");
        return ledger.importBundle(response);
    }
}
