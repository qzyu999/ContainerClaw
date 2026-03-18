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
        self.gateway_url = os.getenv("LLM_GATEWAY_URL", "http://llm-gateway:8000/v1/chat/completions")
        self.model = "gemini-3-flash-preview"

    def _format_history(self, raw_messages):
        formatted = []
        for msg in raw_messages:
            actor = msg['actor_id']
            role = "model" if actor == self.agent_id else "user"
            
            # Restoring your original prefix logic
            if actor == "Moderator":
                text = f"[Moderator Note]: {msg['content']}"
            elif role == "user":
                text = f"{actor}: {msg['content']}"
            else:
                text = msg['content']
            
            formatted.append({"role": role, "parts": [{"text": text}]})
        return formatted

    async def _call_gateway(self, sys_instr, history, is_json=False):
        payload = {
            "system_instruction": sys_instr, # Pass the raw string, let Gateway wrap it
            "contents": self._format_history(history),
            "generationConfig": {"response_mime_type": "application/json"} if is_json else {}
        }
        res = requests.post(self.gateway_url, json=payload)

        if res.status_code == 200:
            return res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        else:
            print(f"❌ API Error {res.status_code}: {res.text}")
            return None

    async def _vote(self, history, candidates, previous_votes=None):
        instr = (
            f"You are {self.agent_id}. Persona: {self.persona}.\n"
            f"Vote for the best agent to respond. Candidates: {candidates}.\n"
            "Respond ONLY in JSON: {'vote': 'name', 'reason': '...', 'is_done': bool, 'done_reason': '...'}"
        )
        if previous_votes:
            instr += f"\n\n### DEBATE MODE ###\nPrevious round results:\n{previous_votes}"
            
        try:
            raw = await self._call_gateway(instr, history, is_json=True)
            # Handle potential Markdown backticks
            if "```" in raw:
                raw = raw.split("```")[1].replace("json", "").strip()
            return json.loads(raw)
        except:
            return {"vote": candidates[0], "is_done": False}

    async def _vote(self, history, candidates, previous_votes=None):
        instr = (
            f"You are {self.agent_id}. Persona: {self.persona}.\n"
            f"Review history and vote for the best candidate: {candidates}.\n"
            "If someone specifically addressed an agent, vote for them.\n"
            "Respond ONLY in JSON: {'vote': 'name', 'reason': '...', 'is_done': bool, 'done_reason': '...'}"
        )
        if previous_votes:
            instr += f"\n\n### DEBATE MODE ###\nPrevious votes: {previous_votes}\nConsensus required."
            
        try:
            raw = await self._call_gateway(instr, history, is_json=True)
            return json.loads(raw)
        except:
            return {"vote": candidates[0], "is_done": False}

    async def _think(self, history):
        instr = f"You are {self.agent_id}. Persona: {self.persona}. Respond or use [WAIT]."
        return await self._call_gateway(instr, history)

class StageModerator:
    def __init__(self, table, agents: List[GeminiAgent], emit_cb: Callable):
        self.table = table
        self.agents = agents
        self.emit_cb = emit_cb
        self.agent_names = [a.agent_id for a in agents]
        self.all_messages = []
        self.history_keys = set()
        self.pa_schema = pa.schema([
            pa.field("ts", pa.int64()), 
            pa.field("actor_id", pa.string()), 
            pa.field("content", pa.string())
        ])

    async def run(self):
        scanner = await self.table.new_scan().create_log_scanner()
        scanner.subscribe_buckets({0: 0})
        self.emit_cb("Moderator", "Multi-Agent System Online.", "thought")

        async for record in scanner:
            row = record.row
            key = f"{row['ts']}-{row['actor_id']}"
            if key not in self.history_keys:
                self.history_keys.add(key)
                self.all_messages.append({"actor_id": row['actor_id'], "content": row['content']})
                self.emit_cb(row['actor_id'], row['content'], "output")
                
                if row['actor_id'] == "Human":
                    await self.handle_election()

    async def elect_leader(self, agents, history, agent_names):
        prev_votes = None
        for r in range(1, 4):
            self.emit_cb("Moderator", f"🗳️ Election Round {r}...", "thought")
            votes = await asyncio.gather(*[a._vote(history, agent_names, prev_votes) for a in agents])
            
            tally = {}
            attr = []
            done_count = 0
            for a, v in zip(agents, votes):
                nominee = v.get('vote', agent_names[0])
                tally[nominee] = tally.get(nominee, 0) + 1
                if v.get('is_done'): done_count += 1
                attr.append(f"{a.agent_id}➜{nominee} ({v.get('reason', 'N/A')})")
            
            self.emit_cb("Moderator", f"Round {r} Tally: {tally}", "thought")
            
            if done_count == len(agents):
                return None, True # Job Done
                
            max_v = max(tally.values())
            winners = [n for n, c in tally.items() if c == max_v]
            if len(winners) == 1:
                return winners[0], False
            
            prev_votes = " | ".join(attr)
            
        return random.choice(winners), False

    async def handle_election(self):
        history = self.all_messages[-20:]
        prev_votes = None
        
        # 3-Round Election Logic
        for r in range(1, 4):
            self.emit_cb("Moderator", f"🗳️ Election Round {r}...", "thought")
            votes = await asyncio.gather(*[a._vote(history, self.agent_names, prev_votes) for a in self.agents])
            
            tally = {}
            attr = []
            done_votes = 0
            for a, v in zip(self.agents, votes):
                nominee = v.get('vote', self.agent_names[0])
                tally[nominee] = tally.get(nominee, 0) + 1
                if v.get('is_done'): done_votes += 1
                attr.append(f"{a.agent_id} voted for {nominee} ({v.get('reason', 'N/A')})")
            
            if done_votes == len(self.agents):
                self.emit_cb("Moderator", "Consensus: Task Complete.", "thought")
                return

            max_v = max(tally.values())
            winners = [n for n, c in tally.items() if c == max_v]
            
            if len(winners) == 1:
                winner = winners[0]
                break
            prev_votes = " | ".join(attr)
        else:
            winner = random.choice(winners)

        self.emit_cb("Moderator", f"🏆 Winner: {winner}", "thought")
        winning_agent = next(a for a in self.agents if a.agent_id == winner)
        
        resp = await winning_agent._think(history)
        
        # Nudge logic if they [WAIT]
        if not resp or "[WAIT]" in resp:
            self.emit_cb("Moderator", f"💤 {winner} is waiting. Nudging...", "thought")
            resp = await winning_agent._think(history + [{"actor_id": "Moderator", "content": f"@{winner}, you were chosen. Why the wait?"}])
            
        if resp:
            await self.publish(winner, resp)

    async def publish(self, actor_id, content):
        writer = self.table.new_append().create_writer()
        batch = pa.RecordBatch.from_arrays([
            pa.array([int(time.time() * 1000)], type=pa.int64()),
            pa.array([actor_id], type=pa.string()),
            pa.array([content], type=pa.string())
        ], schema=self.pa_schema)
        writer.write_arrow_batch(batch)
        await writer.flush()