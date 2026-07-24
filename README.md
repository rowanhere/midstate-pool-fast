# Midstate

Midstate is a cryptocurrency implementing BLAKE3 sequential-time proof of work, post-quantum cryptography (WOTS and MSS), and strict power-of-2 coin denominations. Transactions operate on a two-phase commit-reveal protocol.

## 1. Build

Compilation requires the standard Rust toolchain.

```bash
cargo build --release

```

The binary is output to `./target/release/midstate`.

## Clore VPS Deployment

This setup runs a non-mining Midstate node and a Stratum pool on the same
Clore VPS. Both processes run in `tmux`, so they continue running after the SSH
session closes.

### Clore port forwarding

Add these container ports in the Clore deployment panel:

| Container port | Clore type | Required | Purpose |
| --- | --- | --- | --- |
| `22` | `TCP` | Yes | SSH access |
| `9333` | `TCP` | Yes | Midstate peer-to-peer traffic |
| `3333` | `TCP` | Yes | Public Stratum mining endpoint |
| `8081` | `HTTP` | Yes for a public dashboard | Pool dashboard and audit API |
| `8545` | `HTTP` | No | Node RPC and block explorer; keep private when the pool is on the same VPS |

Clore assigns a public host and port to each `TCP` mapping. For example, a
mapping may display `n1.example.clorecloud.net:1820 -> 3333`. Miners must use
the public host and public port, not container port `3333`:

```text
stratum+tcp://n1.example.clorecloud.net:1820
```

Use the one available Clore `HTTP` mapping for container port `8081`. Clore
terminates TLS and displays a public `https://...` URL, even though the pool
listens with plain HTTP inside the container. The pool and node communicate
locally over `127.0.0.1:8545`, so exposing `8545` is unnecessary and increases
the attack surface.

If a host firewall is active, permit the same inbound service ports:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 9333/tcp
sudo ufw allow 3333/tcp
sudo ufw allow 8081/tcp
```

### Install and build

Run the following as the Clore container's normal administrative user (usually
`root`):

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential cmake pkg-config git curl ca-certificates tmux

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
  | sh -s -- -y
source "$HOME/.cargo/env"
rustup default stable

if [ -d "$HOME/midstate-src/.git" ]; then
  git -C "$HOME/midstate-src" pull --ff-only
else
  git clone https://github.com/rowanhere/midstate-pool-fast.git \
    "$HOME/midstate-src"
fi

cd "$HOME/midstate-src"
cargo build --release
```

### Start the node in tmux

The node command below does not mine. It stores the chain under
`~/midstate-src/data`, listens for peers on `9333`, and exposes RPC only to
processes on the same VPS:

```bash
tmux new-session -d -s midstate-node \
  "cd '$HOME/midstate-src' && \
   RUST_LOG=midstate=info ./target/release/midstate node \
     --data-dir ./data \
     --port 9333 \
     --rpc-port 8545 \
     --rpc-bind 127.0.0.1 \
     2>&1 | tee -a '$HOME/midstate-node.log'"
```

The first launch creates `data/config.toml` with the project's bootstrap peers.
To add another known peer, append one or more complete libp2p multiaddresses
with `--peer`, including the peer ID.

Check the node and sync status:

```bash
tmux capture-pane -pt midstate-node -S -100
tail -f "$HOME/midstate-node.log"

cd "$HOME/midstate-src"
./target/release/midstate state \
  --rpc-host 127.0.0.1 \
  --rpc-port 8545

curl -fsS http://127.0.0.1:8545/state
echo
```

The raw `/state` response should show `"is_syncing":false`, and its height
should agree with a current network peer or explorer before the pool is
started.

Wait for the node to synchronize before starting the pool. The node explorer
is available locally at `http://127.0.0.1:8545` and can be viewed without
publishing RPC by creating an SSH tunnel from a local computer:

```bash
ssh -L 8545:127.0.0.1:8545 -p <PUBLIC_SSH_PORT> \
  root@<CLORE_PUBLIC_HOST>
```

Then open `http://127.0.0.1:8545` locally.

### Start the pool in tmux

Use an MSS address controlled by the pool operator for `<POOL_MSS_ADDRESS>`.
This address is distinct from each miner's payout MSS address.

```bash
POOL_MSS='<POOL_MSS_ADDRESS>'
VERIFY_WORKERS="$(nproc)"

tmux new-session -d -s midstate-pool \
  "cd '$HOME/midstate-src' && \
   RUST_LOG=midstate=info ./target/release/midstate pool \
     --pool-address '$POOL_MSS' \
     --bind-addr 0.0.0.0:3333 \
     --rpc-host 127.0.0.1 \
     --rpc-port 8545 \
     --fee 0 \
     --share-verify-workers '$VERIFY_WORKERS' \
     2>&1 | tee -a '$HOME/midstate-pool.log'"
```

Confirm that the pool has a current job and inspect its logs:

```bash
tmux capture-pane -pt midstate-pool -S -100
tail -f "$HOME/midstate-pool.log"
curl -fsS http://127.0.0.1:8081/pool/stats
```

The logs must report `stratum pool bound to 0.0.0.0:3333` and
`audit api bound to 0.0.0.0:8081`. If either port was already occupied, the
pool may select the next port pair; stop the stale process and restart so the
bound ports continue to match the Clore mappings.

Do not run two pool processes against the same `data/pool_stratum.redb` file.
The pool pays miners directly in a found block's coinbase according to their
credited shares; `pool-wallet.dat` is not used to send those miner payouts.

### Telegram watchdog

Run the watcher in another tmux pane or as a systemd service to get Telegram
alerts when the node, pool, or solo logs emit error-like lines:

```bash
export TELEGRAM_BOT_TOKEN='<bot token>'
export TELEGRAM_CHAT_ID='<chat id>'
export WATCHDOG_LOGS='node:/root/midstate-node.log,pool:/root/midstate-pool.log,solo:/root/midstate-solo.log'

python3 scripts/telegram-watchdog.py
```

The watcher starts at the end of each log by default, so it only alerts on new
issues. Adjust `WATCHDOG_MATCH` if you want a stricter or looser filter.

### Access the pool dashboard

Copy the public HTTPS URL shown by Clore for the container's `8081 HTTP`
mapping, then open:

```text
https://<CLORE_HTTP_HOST>/pool
```

The raw statistics endpoint is:

```text
https://<CLORE_HTTP_HOST>/pool/stats
```

If no Clore HTTP mapping is available, use an SSH tunnel instead:

```bash
ssh -L 8081:127.0.0.1:8081 -p <PUBLIC_SSH_PORT> \
  root@<CLORE_PUBLIC_HOST>
```

Then open `http://127.0.0.1:8081/pool` locally.

### Install and run the official miner

On the mining machine, build the official Midstate repository. The official
miner automatically prefers one usable GPU through `wgpu`/Vulkan and falls
back to CPU mining. It does not distribute work across every GPU in a
multi-GPU machine. `--threads 0` means all available CPU threads if the CPU
fallback is used.

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential cmake pkg-config git curl ca-certificates \
  libvulkan1 vulkan-tools

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
  | sh -s -- -y
source "$HOME/.cargo/env"
rustup default stable

if [ -d "$HOME/midstate-official/.git" ]; then
  git -C "$HOME/midstate-official" pull --ff-only
else
  git clone https://github.com/ciphernom/midstate.git \
    "$HOME/midstate-official"
fi

cd "$HOME/midstate-official"
cargo build --release

./target/release/midstate miner \
  --pool-url stratum+tcp://<CLORE_PUBLIC_HOST>:<PUBLIC_STRATUM_PORT> \
  --payout-address <MINER_MSS_ADDRESS> \
  --worker <WORKER_NAME> \
  --threads 0
```

Use the public endpoint assigned to the `3333 TCP` mapping. Do not use the
dashboard HTTPS URL as `--pool-url`; the pool's mining protocol is Stratum TCP,
not HTTP.

The official miner derives the audit API address from the Stratum hostname and
port. Clore normally gives Stratum and the dashboard different public
endpoints, so the miner may be unable to reach `/api/proof` through Clore's
generated HTTPS hostname even though Stratum mining works. Full proof auditing
requires direct/reverse-proxied access to both services under the port layout
expected by the official client.

### tmux controls

```bash
# List sessions
tmux ls

# Attach to a service
tmux attach -t midstate-node
tmux attach -t midstate-pool

# Detach while leaving it running: press Ctrl+B, then D

# Stop gracefully
tmux send-keys -t midstate-pool C-c
tmux send-keys -t midstate-node C-c
```

## 2. Node Operations

Nodes maintain the state accumulator, manage libp2p networking, and execute mining.

Start a node with mining enabled:

```bash
midstate node --data-dir ./data --port 9333 --rpc-port 8545 --mine --threads 3

```

**Native Batch Explorer**
Access the native explorer at `http://localhost:8545` to view detailed info - mempool, batches, height etc.

**Midstate Axe Dashboard:**
For hardware nodes (or local testing), access the web dashboard at `http://127.0.0.1:8545/axe` to configure Wi-Fi, view live telemetry, and set up pool mining.

## 3. Wallet Operations

The wallet communicates with the node via HTTP RPC. All wallet commands require a password, which is prompted interactively or read from the `MIDSTATE_PASSWORD` environment variable.

Create a new HD (BIP39) wallet:

```bash
midstate wallet create --path wallet.dat

```

Restore a wallet from a 24-word seed phrase:

```bash
midstate wallet restore --path wallet.dat

```

## 4. Addresses & Receiving

Midstate utilizes post-quantum signatures. Standard addresses are consumed upon spending.

**Generate a WOTS Address (Single-use):**

```bash
midstate wallet receive --path wallet.dat --label "payment1"

```

WARNING: REUSING WOTS ADDRESSES EXPOSES YOUR PRIVATE KEY 


**Generate an MSS Address (Multi-use):**

```bash
midstate wallet generate-mss --path wallet.dat --height 10 --label "donation"

```

For mining rewards from a pool, the MSS address type is the ONLY address type you should use.


**Smart Contracts & Covenants:**
Compile a human-readable `.msc` assembly file into a Pay-to-Script-Hash (P2SH) address.

```bash
midstate wallet compile --file limit_order.msc

```

**Scan for Outputs:**
Incoming transactions must be scanned to update local balances:

```bash
midstate wallet scan --path wallet.dat --rpc-port 8545

```

## 5. Sending

Outputs must be powers of 2. The wallet automatically decomposes base-10 integers into power-of-2 denominations, computes change, and executes the required commit and reveal transactions.

**Standard Send:**

```bash
midstate wallet send --path wallet.dat --rpc-port 8545 --to <ADDRESS_HEX>:15

```

**Private Send:**

```bash
midstate wallet send --path wallet.dat --rpc-port 8545 --to <ADDRESS_HEX>:15 --private

```

The `--private` flag splits the payment into separate, independent transactions for each required denomination, preventing output linking.

## 6. P2P CoinJoin

Nodes coordinate uniform-denomination CoinJoin transactions over the p2p network to obfuscate UTXO lineage.

**Initiate a mix:**

```bash
midstate wallet mix --path wallet.dat --rpc-port 8545 --denomination 8

```

This outputs a `<MIX_ID>`.

**Join a mix:**

```bash
midstate wallet mix --path wallet.dat --rpc-port 8545 --denomination 8 --join <MIX_ID>

```

## 7. Mining Rewards

A solo mining node writes its deterministic coinbase seeds to a local log file. These must be imported to the wallet to be spent.

```bash
midstate wallet import-rewards --path wallet.dat --coinbase-file ./data/coinbase_seeds.jsonl

```

## CLI Reference

**Node**

* `node` - Start the node daemon.

**Wallet**

* `wallet create` - Initialize a new HD wallet.
* `wallet restore` - Restore an HD wallet from a 24-word seed phrase.
* `wallet receive` - Generate a WOTS address.
* `wallet generate-mss` - Generate an MSS address.
* `wallet compile` - Compile a `.msc` script.
* `wallet list` - Display controlled coins and unused keys.
* `wallet balance` - Display aggregate balance.
* `wallet scan` - Scan blockchain for incoming coins.
* `wallet send` - Construct and broadcast a transaction.
* `wallet mix` - Participate in a CoinJoin session.
* `wallet history` - Display past transactions.
* `wallet pending` - Display transactions awaiting block inclusion.
* `wallet import-rewards` - Import coinbase seeds from node logs.

**RPC (Debug)**

* `state` - Display chain height, depth, and midstate.
* `mempool` - Display pending transactions.
* `peers` - List active p2p connections.

---

# MidstateLabs


MidstateLabs will be the software company that continues to develop midstate. In addition to the ongoing development, it will be the company responsible for running the bootstraps, formal messaging the community, and selling the up-and-comping MidtateAxe hardware.  

----
