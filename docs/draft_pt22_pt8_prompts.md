create a /.../containerclaw/docs/draft_pt22_pt7.md for the following:

can you analyze 

draft_pt22.md
, 

draft_pt22_pt2.md
, 

draft_pt22_pt3.md
, 

draft_pt22_pt3_continuum.md
, 

draft_pt22_pt4.md
, 

draft_pt22_pt5.md
, and 

draft_pt22_pt6.md
along with the latest changes as of 067ae58101810343a0644c55c083ac8f291cff2e? I am not sure the implementation is good, as i am still not clear on the best product direction/focus/description for this - it started with having the SWE-bench properly have the issue-specific container running for proper session_shell/test_runner tool calls - but it expanded into not just that but thinking how this sidecar pattern can be added for personal and enterprise use cases (thinking of containerclaw as sth that needs to fit to enterprise needs like k8s - while also being relevant to individual users like OpenClaw and Hermes Agent)

can you do a full analysis of the situation, outline what currently exists - what is the best most ideal direction to move towards - and what are the steps to go there. I feel like currently both the agent-centric and human-centric UI/UX are not smooth at all.

Explain it rigorously s.t. the entire process can be derived given the context - basing on system design / architectural review (add mermaid diagrams) - where all code changes need to be thoroughly and exhaustively defended. Start from first principles, and use the speed of light as the limiting factor rather than certain suboptimal design choices.

-------

can you create a /.../containerclaw/docs/draft_pt22_pt8.md as a follow-up. the response is that the 5-agent voting mechanism is a hypothesis that multiagent collaboration and personalities with voting leads to empowering latent intelligence within LLMs along with an evolutionary-algorithm-like process via voting, therefore we need to keep it for the current product - however it's still critical to fix the current core problem of product A, B, and C as mentioned in 

draft_pt22_pt7.md
. reanalyze, go back to the drawing board and draft a plan again in the same fashion that respects these business requirements - while still going for the same ideal of being suited for personal (e.g., openclaw, hermes agent)/enterprise (e.g., openhands, devin) use-cases

Explain it rigorously s.t. the entire process can be derived given the context - basing on system design / architectural review (add mermaid diagrams) - where all code changes need to be thoroughly and exhaustively defended. Start from first principles, and use the speed of light as the limiting factor rather than certain suboptimal design choices.

-----

I would also like to see some product-minded thinking that examines how the config can flow easily from init and through various sessions - the app is still largely me running it once and tearing it down - it has not yet evolved into a smooth UX (either human or agent)-friendly app - I would like the final design to be intuitive and explainable to both humans and agents - not just some layered piece of software that seems to work from the outside