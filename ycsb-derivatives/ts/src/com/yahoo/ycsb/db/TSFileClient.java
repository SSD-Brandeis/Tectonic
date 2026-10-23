package com.yahoo.ycsb.db;

import com.yahoo.ycsb.ByteIterator;
import com.yahoo.ycsb.DB;
import com.yahoo.ycsb.DBException;

import java.io.BufferedWriter;
import java.io.FileWriter;
import java.io.IOException;
import java.sql.Timestamp;
import java.util.*;
import java.util.concurrent.TimeUnit;

/**
 * YCSB-TS binding that records every operation to an X-Bench trace file.
 *
 * A data point maps to one key-value pair in the row-key layout of key-value backed TSDBs such as
 * OpenTSDB: the key is metric:timestamp:tags, with the timestamp zero-padded so that keys sort in
 * time order, and the value is the measurement. Insert is recorded as "I key value". Every time-range
 * query (scan/avg/count/sum, and read, which matches all points of one timestamp) is recorded as a
 * bounded range query "S metric:start metric:end+1" over the keys whose timestamp is in
 * [start, end]; aggregation and tag filtering happen on the returned points.
 */
public class TSFileClient extends DB {
  public static final String FILE_OUTPUT_PROPERTY_KEY = "file.output";
  public static final String FILE_OUTPUT_DEFAULT = "./workload.txt";

  private BufferedWriter writer;

  @Override
  public void init() throws DBException {
    String filename = getProperties().getProperty(FILE_OUTPUT_PROPERTY_KEY, FILE_OUTPUT_DEFAULT);
    try {
      writer = new BufferedWriter(new FileWriter(filename, true), 1 << 20);
    } catch (IOException e) {
      throw new DBException("Failed to open output file", e);
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
  }

  private void writeLine(String line) {
    try {
      writer.write(line);
      writer.newLine();
    } catch (IOException e) {
      System.out.println("Failed to write line: " + line);
    }
  }

  private static String timeKey(String metric, long millis) {
    return metric + ":" + String.format("%013d", millis);
  }

  private void writeRange(String metric, long startMillis, long endMillis) {
    writeLine("S " + timeKey(metric, startMillis) + " " + timeKey(metric, endMillis + 1));
  }

  @Override
  public int read(String metric, Timestamp timestamp, HashMap<String, ArrayList<String>> tags) {
    writeRange(metric, timestamp.getTime(), timestamp.getTime());
    return 0;
  }

  @Override
  public int scan(String metric, Timestamp startTs, Timestamp endTs, HashMap<String, ArrayList<String>> tags,
                  boolean avg, boolean count, boolean sum, int timeValue, TimeUnit timeUnit) {
    writeRange(metric, startTs.getTime(), endTs.getTime());
    return 0;
  }

  @Override
  public int insert(String metric, Timestamp timestamp, double value, HashMap<String, ByteIterator> tags) {
    StringBuilder key = new StringBuilder(timeKey(metric, timestamp.getTime()));
    for (Map.Entry<String, ByteIterator> tag : new TreeMap<>(tags).entrySet()) {
      key.append(':').append(tag.getKey()).append('=').append(tag.getValue().toString());
    }
    writeLine("I " + key + " " + value);
    return 0;
  }
}
