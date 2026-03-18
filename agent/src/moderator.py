import asyncio
import json
import os
import time
import fluss
import pyarrow as pa
import requests
from typing import List, Callable

class GeminiAgent:
    def __init__(self, agent_id, persona, api_key):
        self.agent_id = agent_id
        self.persona = persona
        self.api_key = api_key
        self.gateway_url = os.getenv("LLM_GATEWAY_URL", "http://llm-gateway:8000")
        self.model = "gemini-3-flash-preview"

    def _format_history(self, messages):
        formatted = []
        for msg in messages:
            role = "assistant" if msg['actor_id'] == self.agent_id else "user"
            text = f"{msg['actor_id']}: {msg['content']}" if role == "user" else msg['content']            
            formatted.append({
                "role": role, 
                "content": text
            })
        return formatted

    async def _call_gateway(self, system_instr, history, is_json=False):
        url = f"{self.gateway_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "system_instruction": system_instr,
            "messages": self._format_history(history),
            "api_key": self.api_key,
            "response_mime_type": "application/json" if is_json else "text/plain"
        }
        
        res = requests.post(url, json=payload, timeout=30)
        res.raise_for_status()
        res_json = res.json()
        
        # All responses now come back in Gemini format via our 'Thin' Gateway
        return res_json['candidates'][0]['content']['parts'][0]['text'].strip()

    async def _vote(self, history, candidates):
        try:
            instr = f"Vote for one candidate from {candidates}. Respond ONLY in JSON: {{'vote': 'name', 'is_done': false}}"
            raw_text = await self._call_gateway(instr, history, is_json=True)
            
            # Defensive parsing in case of Markdown backticks
            if "```" in raw_text:
                raw_text = raw_text.split("```")[1].replace("json", "").strip()
            return json.loads(raw_text)
        except:
            return {"vote": candidates[0], "is_done": False}

    async def _think(self, history):
        try:
            instr = f"You are {self.agent_id}. Persona: {self.persona}. Provide a brief response."
            return await self._call_gateway(instr, history)
        except Exception as e:
            return f"[Think Error: {str(e)}]"

    async def _vote(self, history, candidates):
        try:
            instr = f"Vote for one candidate from {candidates}. Respond ONLY in JSON: {{'vote': 'name', 'is_done': false}}"
            raw_text = await self._call_gateway(instr, history, is_json=True)
            
            # Defensive parsing in case of Markdown backticks
            if "```" in raw_text:
                raw_text = raw_text.split("```")[1].replace("json", "").strip()
            return json.loads(raw_text)
        except:
            return {"vote": candidates[0], "is_done": False}

class StageModerator:
    def __init__(self, table, agents: List[GeminiAgent], emit_cb: Callable):
        self.table = table
        self.agents = agents
        self.emit_cb = emit_cb
        self.history_keys = set()
        self.all_messages = []
        self.agent_names = [a.agent_id for a in agents]

        self.pa_schema = pa.schema([
            pa.field("ts", pa.int64()), 
            pa.field("actor_id", pa.string()), 
            pa.field("content", pa.string())
        ])

    async def run(self):
        # Using your feat/424-python-async-iterator AsyncIterator
        scanner = await self.table.new_scan().create_log_scanner()
        scanner.subscribe_buckets({0: fluss.EARLIEST_OFFSET})
        
        self.emit_cb("Moderator", "Multi-Agent System Online. Waiting for input...", "thought")

        async for record in scanner:
            row = record.row
            print(f"👂 Moderator overheard: [{row['actor_id']}] {row['content']}")
            key = f"{row['ts']}-{row['actor_id']}"
            
            if key not in self.history_keys:
                self.history_keys.add(key)
                msg_obj = {"actor_id": row['actor_id'], "content": row['content']}
                self.all_messages.append(msg_obj)
                
                # Pipe everything from Fluss to the App.tsx terminal
                self.emit_cb(row['actor_id'], row['content'], "output")

                if row['actor_id'] == "Human":
                    await self.handle_election()

    async def handle_election(self):
        self.emit_cb("Moderator", "🗳️ Election starting...", "thought")
        context = self.all_messages[-10:]
        
        # Run all votes in parallel
        votes = await asyncio.gather(*[a._vote(context, self.agent_names) for a in self.agents])
        
        tally = {}
        vote_details = []
        for i, v in enumerate(votes):
            voter_name = self.agents[i].agent_id
            voted_for = v.get('vote', 'Unknown')
            tally[voted_for] = tally.get(voted_for, 0) + 1
            vote_details.append(f"{voter_name} ➜ {voted_for}")
        
        # Emit the tally so you see it in the UI!
        self.emit_cb("Moderator", f"Tally: {', '.join(vote_details)}", "thought")
        
        winner_id = max(tally, key=tally.get)
        winner = next(a for a in self.agents if a.agent_id == winner_id)
        
        self.emit_cb("Moderator", f"🏆 Winner: {winner_id}", "thought")
        
        resp = await winner._think(self.all_messages[-10:])
        if resp:
            await self.publish(winner_id, resp)

    async def publish(self, actor_id, content):
        try:
            writer = self.table.new_append().create_writer()
            batch = pa.RecordBatch.from_arrays([
                pa.array([int(time.time() * 1000)], type=pa.int64()),
                pa.array([actor_id], type=pa.string()),
                pa.array([content], type=pa.string())
            ], schema=self.pa_schema) # Use the explicit schema
            
            writer.write_arrow_batch(batch)
            await writer.flush()
            print(f"📝 Successfully published to Fluss: [{actor_id}] {content}")
        except Exception as e:
            print(f"🚨 Moderator Publish Error: {e}")
            raise e