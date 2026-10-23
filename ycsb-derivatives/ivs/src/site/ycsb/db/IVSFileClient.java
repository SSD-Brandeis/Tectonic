package site.ycsb.db;

import site.ycsb.*;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/**
 * YCSB-IVS binding that records every operation to an X-Bench trace file.
 *
 * Same line format as the FileClient used for Table 5 (I/P/U/S/D), plus the extend operation that
 * YCSB-IVS adds to the DB API, which is recorded as an append-merge: "M table:key {field=incr}".
 *
 * The YCSB-IVS experiment scripts derive the run-phase field length histogram from the value size of
 * every record in the database after each extend phase. This binding therefore also keeps the
 * document size of every record (bytes of the MongoDB EJSON document the IVS scripts measure) and
 * persists it between YCSB invocations in the file given by ivs.sizes. For a record with n fields the
 * array holds n raw field lengths (what DB.extend compares against maxfieldlength) followed by n
 * counts of characters that EJSON escapes.
 */
public class IVSFileClient extends DB {
  public static final String FILE_OUTPUT_PROPERTY_KEY = "file.output";
  public static final String FILE_OUTPUT_DEFAULT = "./workload.txt";
  public static final String SIZES_PROPERTY_KEY = "ivs.sizes";
  public static final String SIZES_DEFAULT = "./ivs_sizes.bin";

  // Document sizes are shared by all client threads of one YCSB invocation.
  private static final Object LOCK = new Object();
  private static Map<String, int[]> fieldSizes;
  private static int refCount = 0;
  private static long cappedExtends = 0;

  private BufferedWriter writer;
  private String sizesPath;

  @Override
  public void init() throws DBException {
    String filename = getProperties().getProperty(FILE_OUTPUT_PROPERTY_KEY, FILE_OUTPUT_DEFAULT);
    sizesPath = getProperties().getProperty(SIZES_PROPERTY_KEY, SIZES_DEFAULT);
    try {
      writer = new BufferedWriter(new FileWriter(filename, true), 1 << 20);
    } catch (IOException e) {
      throw new DBException("Failed to open output file", e);
    }
    synchronized (LOCK) {
      if (refCount++ == 0) {
        fieldSizes = loadSizes(sizesPath);
      }
    }
  }

  @Override
  public void cleanup() throws DBException {
    try {
      writer.flush();
      writer.close();
    } catch (IOException e) {
      throw new DBException("Failed to close output file", e);
    }
    synchronized (LOCK) {
      if (--refCount == 0) {
        try {
          saveSizes(sizesPath, fieldSizes);
        } catch (IOException e) {
          throw new DBException("Failed to save record sizes", e);
        }
        if (cappedExtends > 0) {
          System.err.println("[IVSFileClient] extends skipped by maxfieldlength: " + cappedExtends);
        }
      }
    }
  }

  private static Map<String, int[]> loadSizes(String path) throws DBException {
    Map<String, int[]> sizes = new HashMap<>();
    File f = new File(path);
    if (!f.exists()) {
      return sizes;
    }
    try (DataInputStream in = new DataInputStream(new BufferedInputStream(new FileInputStream(f), 1 << 20))) {
      int n = in.readInt();
      for (int i = 0; i < n; i++) {
        String key = in.readUTF();
        int[] fields = new int[in.readInt()];
        for (int j = 0; j < fields.length; j++) {
          fields[j] = in.readInt();
        }
        sizes.put(key, fields);
      }
    } catch (IOException e) {
      throw new DBException("Failed to load record sizes", e);
    }
    return sizes;
  }

  private static void saveSizes(String path, Map<String, int[]> sizes) throws IOException {
    try (DataOutputStream out = new DataOutputStream(new BufferedOutputStream(new FileOutputStream(path), 1 << 20))) {
      out.writeInt(sizes.size());
      for (Map.Entry<String, int[]> e : sizes.entrySet()) {
        out.writeUTF(e.getKey());
        out.writeInt(e.getValue().length);
        for (int v : e.getValue()) {
          out.writeInt(v);
        }
      }
    }
  }

  /** Characters of the value that need an escape inside a JSON string: '"' and '\'. */
  private static int escapes(String value) {
    int n = 0;
    for (int i = 0; i < value.length(); i++) {
      char c = value.charAt(i);
      if (c == '"' || c == '\\') {
        n++;
      }
    }
    return n;
  }

  private static int fieldIndex(String field) {
    return Integer.parseInt(field.substring("field".length()));
  }

  private void writeLine(String line) {
    try {
      writer.write(line);
      writer.newLine();
    } catch (IOException e) {
      System.out.println("Failed to write line: " + line);
    }
  }

  /** Same rendering as FileClient; values are materialized once so they can be measured. */
  private static String formatMap(Map<String, String> map) {
    StringBuilder sb = new StringBuilder();
    sb.append("{");
    for (Map.Entry<String, String> entry : map.entrySet()) {
      sb.append(entry.getKey()).append("=").append(entry.getValue()).append(", ");
    }
    if (!map.isEmpty()) {
      sb.setLength(sb.length() - 2);
    }
    sb.append("}");
    return sb.toString();
  }

  private static Map<String, String> materialize(Map<String, ByteIterator> values) {
    Map<String, String> out = new LinkedHashMap<>();
    for (Map.Entry<String, ByteIterator> e : values.entrySet()) {
      out.put(e.getKey(), e.getValue().toString());
    }
    return out;
  }

  @Override
  public Status read(String table, String key, Set<String> fields, Map<String, ByteIterator> result) {
    writeLine("P " + table + ":" + key);
    return Status.OK;
  }

  @Override
  public Status insert(String table, String key, Map<String, ByteIterator> values) {
    Map<String, String> vals = materialize(values);
    int n = vals.size();
    int[] sizes = new int[2 * n];
    for (Map.Entry<String, String> e : vals.entrySet()) {
      int idx = fieldIndex(e.getKey());
      sizes[idx] = e.getValue().length();
      sizes[n + idx] = escapes(e.getValue());
    }
    synchronized (LOCK) {
      fieldSizes.put(key, sizes);
    }
    writeLine("I " + table + ":" + key + " " + formatMap(vals));
    return Status.OK;
  }

  @Override
  public Status update(String table, String key, Map<String, ByteIterator> values) {
    Map<String, String> vals = materialize(values);
    synchronized (LOCK) {
      int[] sizes = fieldSizes.get(key);
      if (sizes != null) {
        int n = sizes.length / 2;
        for (Map.Entry<String, String> e : vals.entrySet()) {
          int idx = fieldIndex(e.getKey());
          sizes[idx] = e.getValue().length();
          sizes[n + idx] = escapes(e.getValue());
        }
      }
    }
    writeLine("U " + table + ":" + key + " " + formatMap(vals));
    return Status.OK;
  }

  /**
   * DB.extend in YCSB-IVS reads each field and writes back orig + incr unless the result would reach
   * maxfieldlength. Recorded as one append-merge carrying the increment.
   */
  @Override
  public Status extend(String table, String key, Map<String, ByteIterator> values, long maxfieldlength) {
    Map<String, String> vals = materialize(values);
    synchronized (LOCK) {
      int[] sizes = fieldSizes.get(key);
      if (sizes != null) {
        int n = sizes.length / 2;
        for (Iterator<Map.Entry<String, String>> it = vals.entrySet().iterator(); it.hasNext();) {
          Map.Entry<String, String> e = it.next();
          int idx = fieldIndex(e.getKey());
          if (sizes[idx] + e.getValue().length() < maxfieldlength) {
            sizes[idx] += e.getValue().length();
            sizes[n + idx] += escapes(e.getValue());
          } else {
            cappedExtends++;
            it.remove();
          }
        }
      }
    }
    if (vals.isEmpty()) {
      // Every field was at maxfieldlength: YCSB-IVS still reads and rewrites the record unchanged.
      writeLine("P " + table + ":" + key);
      return Status.OK;
    }
    writeLine("M " + table + ":" + key + " " + formatMap(vals));
    return Status.OK;
  }

  @Override
  public Status delete(String table, String key) {
    writeLine("D " + table + ":" + key);
    return Status.OK;
  }

  @Override
  public Status scan(String table, String startkey, int recordcount, Set<String> fields,
                     Vector<HashMap<String, ByteIterator>> result) {
    writeLine("S " + table + ":" + startkey + " " + recordcount);
    return Status.OK;
  }

  /**
   * Writes "key,size" for every record, where size is the byte length of the MongoDB EJSON document
   * {"field0":"...",...} that experiment_mongodb.sh measures to build the histogram.
   */
  public static void main(String[] args) throws Exception {
    Map<String, int[]> sizes = loadSizes(args[0]);
    try (PrintWriter out = new PrintWriter(new BufferedWriter(new FileWriter(args[1]), 1 << 20))) {
      out.println("key,size");
      for (Map.Entry<String, int[]> e : sizes.entrySet()) {
        int[] f = e.getValue();
        int n = f.length / 2;
        long size = 2 + (n - 1);
        for (int i = 0; i < n; i++) {
          size += ("\"field" + i + "\":\"\"").length() + f[i] + f[n + i];
        }
        out.println(e.getKey() + "," + size);
      }
    }
  }
}
