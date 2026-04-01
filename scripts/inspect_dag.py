import asyncio
import os
import sys
import pprint
import fluss

async def inspect():
    is_docker = os.path.exists("/.dockerenv")
    host = "coordinator-server" if is_docker else "localhost"
    sid = sys.argv[1] if len(sys.argv) > 1 else "default-session"

    print(f"🛰️  Connecting to Fluss at {host}:9123 (session: {sid})...")
    
    try:
        config = fluss.Config({"bootstrap.servers": f"{host}:9123"})
        conn = await fluss.FlussConnection.create(config)
        admin = await conn.get_admin()
        tables = await admin.list_tables("containerclaw")
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return

    for table_name in tables:
        print(f"\n" + "="*70)
        print(f" 📊 TABLE: containerclaw.{table_name}")
        print("="*70)
        
        try:
            path = fluss.TablePath("containerclaw", table_name)
            table = await conn.get_table(path)
            info = await admin.get_table_info(path)
            
            # 1. Primary Key Tables (Point Lookups Only)
            if table.has_primary_key():
                print("   Type: Primary Key Table (Python SDK can only lookup by specific key)")
                try:
                    lookuper = table.new_lookup().create_lookuper()
                    res = await lookuper.lookup({"session_id": sid})
                    if res:
                        print(f"   ✅ Data for '{sid}':")
                        pprint.pprint(res, indent=5, width=120)
                    else:
                        print(f"   ⚠️ No data found for '{sid}'")
                except Exception as e:
                    print(f"   ℹ️  Requires full composite PK for lookup. Error: {e}")
            
            # 2. Log Tables (Full Historical Scan)
            else:
                print("   Type: Log Table (Scanning all historical data...)")
                scanner = await table.new_scan().create_record_batch_log_scanner()
                
                # CHANGED: Subscribe to EARLIEST_OFFSET to get everything from the start
                scanner.subscribe_buckets({b: fluss.EARLIEST_OFFSET for b in range(info.num_buckets)})
                
                empty_polls = 0
                total_batches = 0
                
                # Poll repeatedly until the stream runs dry
                while empty_polls < 3: 
                    batches = await scanner._async_poll_batches(500)
                    if not batches:
                        empty_polls += 1
                        continue
                    
                    empty_polls = 0
                    total_batches += len(batches)
                    for b in batches:
                        # Print the full dictionary of arrays for the batch
                        pprint.pprint(b.batch.to_pydict(), indent=5, width=120)
                
                if total_batches == 0:
                    print("   (Table is empty)")
                else:
                    print(f"   ✅ Finished scanning {total_batches} batches.")

        except Exception as e:
            print(f"   ❌ Table Error: {e}")

    conn.close()

if __name__ == "__main__":
    asyncio.run(inspect())