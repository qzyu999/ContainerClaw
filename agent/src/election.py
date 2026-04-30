"""
Election protocol for ContainerClaw multi-agent system.

Implements a 3-round democratic election where agents vote for the
best-suited responder. Includes a debate tie-breaker mechanism where
agents must defend or concede their votes based on others' reasoning.
"""

import asyncio
import random
from typing import Awaitable, Callable


class ElectionProtocol:
    """Run democratic elections among agents to decide who acts next.
    
    The protocol runs up to 3 rounds:
    1. Initial vote — each agent votes based on the conversation
    2. Tie-breaker with debate — agents see others' reasoning and re-vote
    3. Final round — if still tied, random circuit breaker chooses
    """

    async def run_election(
        self,
        agents,
        roster_str: str,
        history: list[dict],
        publish_fn: Callable[..., Awaitable[None]],
        parent_event_id: str = "",
    ) -> tuple[str | None, str, bool]:
        """Run a 3-round election.
        
        Args:
            agents: List of GeminiAgent instances.
            roster_str: Human-readable roster (e.g., "Alice (architect), Bob (PM)").
            history: Context window messages for voting context.
            publish_fn: Async callback to publish events to Fluss.
            parent_event_id: Causal parent for election status messages.
        
        Returns:
            (winner, election_log, is_job_done) where:
            - winner: Agent name or None if consensus is "task complete"
            - election_log: Multi-line string summarizing all rounds
            - is_job_done: True if all agents agree the task is finished
        """
        agent_names = [a.agent_id for a in agents]
        previous_votes_context = None
        election_log_collector = []

        for r in range(1, 4):
            election_log_collector.append(f"--- Round {r} ---")
            await publish_fn("Moderator", f"🗳️ Election Round {r}...", "thought",
                             parent_event_id=parent_event_id, edge_type="SEQUENTIAL")
            print(f"🗳️ [Moderator] Election Round {r} starting...")

            # Stagger votes with random jitter to avoid thundering-herd SSL drops
            async def _staggered_vote(agent, delay):
                await asyncio.sleep(delay)
                return await agent._vote(history, roster_str, previous_votes_context)

            jittered = [
                _staggered_vote(a, random.uniform(0, 2.0))
                for a in agents
            ]
            votes = await asyncio.gather(*jittered)

            tally = {}
            attribution_list = []
            valid_votes_count = 0
            done_votes_count = 0

            for agent, vote_result in zip(agents, votes):
                if vote_result and "vote" in vote_result:
                    valid_votes_count += 1
                    nominee = vote_result['vote']
                    reason = vote_result.get('reason', 'N/A')

                    # Defensively parse the boolean in case the LLM returns a string "true"
                    is_done_raw = vote_result.get('is_done', False)
                    is_done = is_done_raw.lower() == 'true' if isinstance(is_done_raw, str) else bool(is_done_raw)
                    done_reason = vote_result.get('done_reason', 'N/A')

                    if is_done:
                        done_votes_count += 1

                    tally[nominee] = tally.get(nominee, 0) + 1
                    vote_str = f"{agent.agent_id} voted for {nominee} ('{reason}') | Done: {is_done} ('{done_reason}')"
                    attribution_list.append(vote_str)
                    election_log_collector.append(vote_str)
                    print(f"🗣️ [{agent.agent_id}] voted for {nominee} -> \"{reason}\" | Done: {is_done} -> \"{done_reason}\"")
                else:
                    print(f"⚠️ [{agent.agent_id}] failed to cast a valid vote.")

            if valid_votes_count == 0:
                return random.choice(agent_names), "No valid votes received.", False

            # Check for unanimous agreement that the job is done
            is_job_done = (done_votes_count == valid_votes_count) and (valid_votes_count > 0)

            tally_str = f"Tally: {tally}"
            election_log_collector.append(tally_str)
            await publish_fn("Moderator", f"Round {r} {tally_str}", "thought",
                             parent_event_id=parent_event_id, edge_type="SEQUENTIAL")
            print(f"📊 [Moderator] Round {r} {tally_str}")

            # If everyone agrees the task is finished, return immediately
            if is_job_done:
                election_log_collector.append("Consensus reached: Task is complete.")
                print("✅ [Moderator] All agents agree the job is completed.")
                return None, "\n".join(election_log_collector), True

            max_votes = max(tally.values())
            winners = [name for name, count in tally.items() if count == max_votes]

            if len(winners) == 1:
                return winners[0], "\n".join(election_log_collector), False

            previous_votes_context = " | ".join(attribution_list)
            print(f"⚖️ [Moderator] Round {r} ended in a tie: {winners}")

        choice = random.choice(winners)
        election_log_collector.append(f"Tie persists. Circuit breaker chose: {choice}")
        print(f"🎲 [Moderator] Tie persists. Circuit breaker choosing: {choice}")
        return choice, "\n".join(election_log_collector), False
