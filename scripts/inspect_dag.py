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

    # --- PRE-SCAN PHASE: Discover all dynamic keys ---
    print(f"\n🔍 Pre-scanning chatroom to discover subagents and events...")
    known_actors = {"Moderator", "Alice", "Bob", "Carol", "David", "Eve", "Human"}
    known_child_ids = set()
    
    try:
        chat_path = fluss.TablePath("containerclaw", "chatroom")
        chat_table = await conn.get_table(chat_path)
        chat_info = await admin.get_table_info(chat_path)
        
        scanner = await chat_table.new_scan().create_record_batch_log_scanner()
        scanner.subscribe_buckets({b: fluss.EARLIEST_OFFSET for b in range(chat_info.num_buckets)})
        
        empty_polls = 0
        while empty_polls < 3:
            batches = await scanner._async_poll_batches(500)
            if not batches:
                empty_polls += 1
                continue
            empty_polls = 0
            for b in batches:
                d = b.batch.to_pydict()
                for i in range(len(d.get("actor_id", []))):
                    if d["session_id"][i] == sid:
                        actor = d["actor_id"][i]
                        event_id = d["event_id"][i]
                        known_actors.add(actor)
                        known_child_ids.add(f"{actor}|{event_id}")
    except Exception as e:
        print(f"⚠️ Pre-scan failed: {e}")

    print(f"✅ Discovered {len(known_actors)} actors and {len(known_child_ids)} events.")

    # --- INSPECTION PHASE ---
    for table_name in tables:
        print(f"\n" + "="*70)
        print(f" 📊 TABLE: containerclaw.{table_name}")
        print("="*70)
        
        try:
            path = fluss.TablePath("containerclaw", table_name)
            table = await conn.get_table(path)
            info = await admin.get_table_info(path)
            schema_cols = [c[0] for c in info.get_schema().get_columns()]
            
            # 1. Primary Key Tables (Point Lookups)
            if table.has_primary_key():
                print("   Type: Primary Key Table (Python SDK can only lookup by specific key)")
                lookuper = table.new_lookup().create_lookuper()
                
                if "child_id" in schema_cols:
                    print(f"   ℹ️  Looking up {len(known_child_ids)} known event IDs...")
                    found = 0
                    for cid in known_child_ids:
                        res = await lookuper.lookup({"session_id": sid, "child_id": cid})
                        if res:
                            pprint.pprint(res, indent=5, width=120)
                            found += 1
                    print(f"   ✅ Found {found} matching rows.")
                            
                elif "actor_id" in schema_cols or "agent_id" in schema_cols:
                    key_field = "actor_id" if "actor_id" in schema_cols else "agent_id"
                    print(f"   ℹ️  Looking up {len(known_actors)} known actors...")
                    found = 0
                    for actor in known_actors:
                        res = await lookuper.lookup({"session_id": sid, key_field: actor})
                        if res:
                            pprint.pprint(res, indent=5, width=120)
                            found += 1
                    print(f"   ✅ Found {found} matching rows.")

                else:
                    res = await lookuper.lookup({"session_id": sid})
                    if res:
                        print(f"   ✅ Data for '{sid}':")
                        pprint.pprint(res, indent=5, width=120)
                    else:
                        print(f"   ⚠️ No data found for '{sid}'")
            
            # 2. Log Tables (Full Historical Scan)
            else:
                scanner = await table.new_scan().create_record_batch_log_scanner()
                scanner.subscribe_buckets({b: fluss.EARLIEST_OFFSET for b in range(info.num_buckets)})
                
                empty_polls = 0
                total_batches = 0
                
                # --- SPECIAL HANDLING FOR BULKY AGENT_STATUS ---
                if table_name == "agent_status":
                    print("   Type: Log Table (Scanning all, but only showing the top 5 most recent...)")
                    all_records = []
                    while empty_polls < 3: 
                        batches = await scanner._async_poll_batches(500)
                        if not batches:
                            empty_polls += 1
                            continue
                        empty_polls = 0
                        total_batches += len(batches)
                        for b in batches:
                            d = b.batch.to_pydict()
                            keys = list(d.keys())
                            if keys:
                                # Convert column-arrays into individual row dictionaries
                                for i in range(len(d[keys[0]])):
                                    all_records.append({k: d[k][i] for k in keys})
                    
                    if not all_records:
                        print("   (Table is empty)")
                    else:
                        print(f"   ✅ Found {len(all_records)} total heartbeats across {total_batches} batches.")
                        print("   showing latest 5:")
                        for row in all_records[-5:]:
                            pprint.pprint(row, indent=5, width=120)

                # --- STANDARD LOG TABLES WITH TS SORTING ---
                all_rows = []
                empty_polls = 0

                while empty_polls < 3:
                    batches = await scanner._async_poll_batches(500)
                    if not batches:
                        empty_polls += 1
                        continue
                    empty_polls = 0

                    for b in batches:
                        d = b.batch.to_pydict()
                        keys = list(d.keys())
                        if keys:
                            for i in range(len(d[keys[0]])):
                                all_rows.append({k: d[k][i] for k in keys})

                # --- SAFE SORTING ---
                if not all_rows:
                    print("   (Table is empty)")
                else:
                    try:
                        if "ts" in all_rows[0]:
                            all_rows.sort(key=lambda r: r.get("ts", 0))
                            print("   🔃 Sorted by 'ts'")
                        else:
                            print("   ⚠️ No 'ts' column found — printing unsorted")
                    except Exception as e:
                        print(f"   ⚠️ Sorting failed: {e} — printing unsorted")

                    for row in all_rows:
                        pprint.pprint(row, indent=5, width=120)

        except Exception as e:
            print(f"   ❌ Table Error: {e}")

    conn.close()

if __name__ == "__main__":
    asyncio.run(inspect())