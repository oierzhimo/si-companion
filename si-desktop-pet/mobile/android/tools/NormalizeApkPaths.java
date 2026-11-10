import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.HashSet;
import java.util.Set;
import java.util.zip.CRC32;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

/**
 * Rewrites APK entry names with POSIX separators before zipalign and signing.
 *
 * <p>Run with the JDK 17 single-file source launcher:
 * {@code java NormalizeApkPaths.java unsigned.apk normalized.apk}. Only the
 * standard library is used. The input APK is opened read-only. The output is
 * written to a temporary file next to the target and moved into place; no
 * directory is ever deleted.
 */
public final class NormalizeApkPaths {
    private static final int BUFFER_SIZE=65536;

    private NormalizeApkPaths() {}

    public static void main(String[] args) throws IOException {
        if(args.length!=2) {
            System.err.println("Usage: java NormalizeApkPaths.java <unsigned.apk> <normalized.apk>");
            System.exit(2);
        }
        Path input=Path.of(args[0]).toAbsolutePath().normalize();
        Path output=Path.of(args[1]).toAbsolutePath().normalize();
        if(!Files.isRegularFile(input)) throw new IOException("Input APK is not a file: "+input);
        if(input.equals(output)) throw new IOException("Input and output must be different paths: "+input);
        if(Files.exists(output) && Files.isSameFile(input,output)) {
            throw new IOException("Input and output refer to the same file: "+input);
        }
        Path parent=output.getParent();
        if(parent==null || !Files.isDirectory(parent)) {
            throw new IOException("Output directory does not exist: "+parent);
        }
        Path temporary=parent.resolve(output.getFileName()+".tmp-"+ProcessHandle.current().pid()+"-"+System.nanoTime());

        int total=0;
        int renamed=0;
        Set<String> names=new HashSet<>();
        try {
            try(ZipInputStream in=new ZipInputStream(
                        new BufferedInputStream(Files.newInputStream(input)),StandardCharsets.UTF_8);
                ZipOutputStream out=new ZipOutputStream(
                        new BufferedOutputStream(Files.newOutputStream(temporary)),StandardCharsets.UTF_8)) {
                byte[] buffer=new byte[BUFFER_SIZE];
                ZipEntry entry;
                while((entry=in.getNextEntry())!=null) {
                    total++;
                    String original=entry.getName();
                    String normalized=original.replace('\\','/');
                    validate(normalized,original);
                    if(!names.add(normalized)) {
                        throw new IOException("Duplicate entry name after normalization: "+normalized);
                    }
                    if(!normalized.equals(original)) renamed++;

                    int method=entry.getMethod();
                    if(method!=ZipEntry.STORED && method!=ZipEntry.DEFLATED) {
                        throw new IOException("Unsupported compression method "+method+" for entry: "+original);
                    }
                    ZipEntry copy=new ZipEntry(normalized);
                    copy.setMethod(method);
                    if(entry.getTime()!=-1) copy.setTime(entry.getTime());
                    if(method==ZipEntry.STORED) {
                        byte[] data=readFully(in);
                        CRC32 crc=new CRC32();
                        crc.update(data);
                        copy.setSize(data.length);
                        copy.setCompressedSize(data.length);
                        copy.setCrc(crc.getValue());
                        out.putNextEntry(copy);
                        out.write(data);
                    } else {
                        out.putNextEntry(copy);
                        int count;
                        while((count=in.read(buffer))!=-1) out.write(buffer,0,count);
                    }
                    out.closeEntry();
                    in.closeEntry();
                }
            }
            if(total==0) throw new IOException("Input APK contains no entries: "+input);
            try {
                Files.move(temporary,output,
                        StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
            } catch(AtomicMoveNotSupportedException unsupported) {
                Files.move(temporary,output,StandardCopyOption.REPLACE_EXISTING);
            }
        } catch(IOException | RuntimeException error) {
            Files.deleteIfExists(temporary);
            throw error;
        }
        System.out.println("Normalized "+renamed+" of "+total+" APK entry name(s) to POSIX separators; wrote "+output);
    }

    private static void validate(String name,String original) throws IOException {
        if(name.isEmpty()) throw new IOException("Empty APK entry name");
        if(name.startsWith("/")) {
            throw new IOException("Absolute APK entry name rejected: "+original);
        }
        if(name.length()>=2 && Character.isLetter(name.charAt(0)) && name.charAt(1)==':') {
            throw new IOException("Drive-qualified APK entry name rejected: "+original);
        }
        if(name.indexOf('\0')>=0) throw new IOException("APK entry name contains NUL: "+original);
        String body=name.endsWith("/") ? name.substring(0,name.length()-1) : name;
        if(body.isEmpty()) throw new IOException("APK entry name has no file component: "+original);
        for(String segment:body.split("/",-1)) {
            if(segment.isEmpty()) {
                throw new IOException("APK entry name contains an empty segment: "+original);
            }
            if(segment.equals("..") || segment.equals(".")) {
                throw new IOException("APK entry name contains a relative path segment: "+original);
            }
        }
    }

    private static byte[] readFully(InputStream input) throws IOException {
        ByteArrayOutputStream output=new ByteArrayOutputStream();
        byte[] buffer=new byte[BUFFER_SIZE];
        int count;
        while((count=input.read(buffer))!=-1) output.write(buffer,0,count);
        return output.toByteArray();
    }
}
