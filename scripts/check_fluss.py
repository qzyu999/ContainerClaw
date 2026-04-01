import asyncio
import json
import os
import sys
import pprint
import fluss

async def check_fluss(sid="default-session"):
    print(f"🛰️ Connecting to Fluss...")
    
    # Internal DNS for running inside Docker
    config = fluss.Config({"bootstrap.servers": "coordinator-server:9123"})
    conn = await fluss.FlussConnection.create(config)
    admin = await conn.get_admin()

    try:
        tables = await admin.list_tables("containerclaw")
        print(f"📋 Available Tables: {tables}")
    except Exception as e:
        print(f"❌ Error listing tables: {e}")
        return

    for table_name in tables:
        print(f"\n" + "="*60)
        print(f" 📊 TABLE: containerclaw.{table_name}")
        print("="*60)
        
        try:
            path = fluss.TablePath("containerclaw", table_name)
            table = await conn.get_table(path)
            info = await admin.get_table_info(path)
            
            print(f"   Schema: {[c[0] for c in info.schema.get_columns()]}")
            
            if table.has_primary_key():
                # For PK tables, we can't easily scan all rows in Python yet
                # but we can try a lookup if we have the full PK.
                # Since we don't have the full PK here, we'll just note it.
                print("   Type: Primary Key Table (Indexed)")
                
                # Check for actor_heads since we might have a session_id
                if table_name == "dag_summaries" or table_name == "live_metrics":
                    lookuper = table.new_lookup().create_lookuper()
                    res = await lookuper.lookup({"session_id": sid})
                    if res:
                        print(f"   ✅ Data for '{sid}': FOUND")
                        pprint.pprint(res, indent=5, width=100)
                    else:
                        print(f"   ⚠️ Data for '{sid}': NOT FOUND")
                else:
                    print(f"   ℹ️ Point lookup requires full PK (session_id + ...)")
            
            # Log Scanning check
            if table_name == "dag_events":
                print("\n   🕒 Tailing new events (LATEST)...")
                scanner = await table.new_scan().create_record_batch_log_scanner()
                scanner.subscribe_buckets({b: fluss.LATEST_OFFSET for b in range(info.num_buckets)})
                batches = await scanner._async_poll_batches(100)
                if not batches:
                    print("   (Empty - no new events since start of check)")
                else:
                    print(f"   ✅ Found {len(batches)} batches")

        except Exception as e:
            print(f"   ❌ Error: {e}")

if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "default-session"
    asyncio.run(check_fluss(sid))
