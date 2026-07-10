"""vivijure-local-16gb: the local-consumer render engine (the CogVideoX door).

The fidelity sibling of the LTX door (vivijure-local-12gb) and the deliberate opposite of
vivijure-backend (the RunPod datacenter engine). Runs CogVideoX-5B-I2V image-to-video on a single
consumer GPU and speaks the SAME i2v_clip job contract, so the studio's local-gpu module plugs it into
the unchanged control plane. The 16GB floor is PROVEN on real silicon (docs/proof/RESULTS.md),
exactly like LTX's 12GB proof. See docs/architecture.md.
"""

__version__ = "0.2.0"
