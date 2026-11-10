package cn.si.companion;

import org.json.JSONArray;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;

/** The protocol's UTF-8 JSON encoding, matching Python ensure_ascii=False. */
public final class JsonCodec {
    public static JSONObject parse(byte[] bytes) throws Exception {
        String text=StandardCharsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
            .onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT).decode(java.nio.ByteBuffer.wrap(bytes)).toString();
        Syntax syntax=new Syntax(text);syntax.value(0);syntax.space();
        if(syntax.position!=text.length()) throw new IllegalArgumentException("Trailing JSON data");
        return new JSONObject(text);
    }
    private static final class Syntax {
        static final java.util.regex.Pattern NUMBER=java.util.regex.Pattern.compile("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?");
        final String text;int position=0;
        Syntax(String text) { this.text=text; }
        void bad() { throw new IllegalArgumentException("Invalid JSON syntax"); }
        void space() { while(position<text.length() && " \t\r\n".indexOf(text.charAt(position))>=0)position++; }
        boolean take(char wanted) { space();if(position<text.length() && text.charAt(position)==wanted) { position++;return true; }return false; }
        void string() {
            if(!take('"'))bad();
            while(position<text.length()) {
                char c=text.charAt(position++);if(c=='"')return;if(c<32)bad();
                if(c=='\\') {
                    if(position>=text.length())bad();char escape=text.charAt(position++);
                    if(escape=='u') { for(int n=0;n<4;n++) { if(position>=text.length() || "0123456789abcdefABCDEF".indexOf(text.charAt(position++))<0)bad(); } }
                    else if("\"\\/bfnrt".indexOf(escape)<0)bad();
                }
            }
            bad();
        }
        void value(int depth) {
            if(depth>20)bad();space();if(position>=text.length())bad();char c=text.charAt(position);
            if(c=='{') {
                position++;if(take('}'))return;
                do { string();if(!take(':'))bad();value(depth+1);if(take('}'))return; }while(take(','));bad();
            } else if(c=='[') {
                position++;if(take(']'))return;
                do { value(depth+1);if(take(']'))return; }while(take(','));bad();
            } else if(c=='"')string();
            else {
                for(String token:new String[]{"true","false","null"}) if(text.startsWith(token,position)) { position+=token.length();return; }
                java.util.regex.Matcher number=NUMBER.matcher(text).region(position,text.length());
                if(!number.lookingAt())bad();position=number.end();
            }
        }
    }
    public static String canonical(Object value) throws Exception {
        if (value == null || value == JSONObject.NULL) return "null";
        if (value instanceof String) return quote((String)value);
        if (value instanceof Boolean) return value.toString();
        if (value instanceof Integer || value instanceof Long) return value.toString();
        if (value instanceof JSONObject) {
            JSONObject object=(JSONObject)value;
            ArrayList<String> keys=new ArrayList<>();
            java.util.Iterator<String> iterator=object.keys();
            while(iterator.hasNext()) keys.add(iterator.next());
            Collections.sort(keys);
            StringBuilder result=new StringBuilder("{");
            for(String key:keys) {
                if(result.length()>1) result.append(',');
                result.append(quote(key)).append(':').append(canonical(object.get(key)));
            }
            return result.append('}').toString();
        }
        if (value instanceof JSONArray) {
            JSONArray array=(JSONArray)value;
            StringBuilder result=new StringBuilder("[");
            for(int i=0;i<array.length();i++) {
                if(i>0) result.append(',');
                result.append(canonical(array.get(i)));
            }
            return result.append(']').toString();
        }
        throw new IllegalArgumentException("Unsupported JSON number or value");
    }

    private static String quote(String value) {
        StringBuilder result=new StringBuilder("\"");
        for(int i=0;i<value.length();i++) {
            char c=value.charAt(i);
            switch(c) {
                case '"': result.append("\\\"");break;
                case '\\': result.append("\\\\");break;
                case '\b': result.append("\\b");break;
                case '\f': result.append("\\f");break;
                case '\n': result.append("\\n");break;
                case '\r': result.append("\\r");break;
                case '\t': result.append("\\t");break;
                default:
                    if(c<32) result.append(String.format(java.util.Locale.ROOT,"\\u%04x",(int)c));
                    else if(Character.isHighSurrogate(c)) {
                        if(i+1>=value.length() || !Character.isLowSurrogate(value.charAt(i+1)))
                            throw new IllegalArgumentException("Unpaired Unicode surrogate");
                        result.append(c).append(value.charAt(++i));
                    } else if(Character.isLowSurrogate(c)) throw new IllegalArgumentException("Unpaired Unicode surrogate");
                    else result.append(c);
            }
        }
        return result.append('"').toString();
    }

    public static byte[] bytes(Object value) throws Exception {
        return canonical(value).getBytes(StandardCharsets.UTF_8);
    }

    public static String sha256(byte[] value) throws Exception {
        byte[] digest=MessageDigest.getInstance("SHA-256").digest(value);
        StringBuilder result=new StringBuilder();
        for(byte part:digest) result.append(String.format(java.util.Locale.ROOT,"%02x",part&255));
        return result.toString();
    }
}
