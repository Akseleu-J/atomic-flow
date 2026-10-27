# Requests timeout topology: fixed synthetic qualification

Prospective host-owned probe; no Requests source imports, setup, tests, model calls,
or accepted calibration. The CLI has only an output path. Destination, image,
interpreter, resource ceilings and network operations are fixed in source.

## Concrete blocker

Original artifact12649834300, calibration/gold.log, records both
TestTimeout.test_connect_timeout and test_total_timeout_connect connecting to
10.255.255.1:80 with connect timeout0.1. Network-none has no route and yields
ENETUNREACH immediately, so the historical expectation of ConnectTimeout fails.
A server accepting TCP and withholding HTTP would test read timeout instead and
cannot repair this requirement.

## Hermetic topology

Keep the worker on Docker network none, no host mounts, all capabilities dropped,
no-new-privileges, non-root UID65534, read-only image, bounded tmpfs and resources.
Only the trusted Linux host setup runs as root. It opens and pins the owned
container network namespace descriptor, verifies its inode differs from the host
namespace and binds it to the Docker PID/start-time receipt. Host nsenter enters
only that pinned network namespace; PID reuse cannot redirect the setup into an
unrelated namespace. The target receives neither NET_ADMIN nor a namespace FD.

The host adds one route10.255.255.1/32 via lo, source127.0.0.1, and an OUTPUT
iptables rule silently dropping TCP destination10.255.255.1:80. The route lets a
real kernel TCP connect start; the DROP prevents its SYN from completing. There
is no bridge, veth, default route, proxy or external service. Other non-loopback
IPs remain unroutable. Existing loopback HTTPBin HTTP/HTTPS stays available.
The added route is explicitly disclosed; network-none is the Docker mode, not a
claim that the namespace routing table was left unmodified.

A fixed stdlib-only synthetic program, using isolated Python outside /testbed,
proves a real loopback server/client exchange and socket.timeout near0.1seconds.
Host evidence retains routing tables, absent route to1.1.1.1, exact DROP-rule
presence, a nonzero dropped-packet counter, image identity, unchanged namespace
identity and confirmed owned-container removal. No target-provided success
marker can replace this host receipt. Every setup/probe/cleanup error refuses
qualification. Container removal releases its namespace after the host closes
its pinned descriptor; no global host firewall or route is changed.

## Actual qualification still required

Existing core-ci-1146f07 Docker evidence records Docker28.0.4, rootful Ubuntu24.04,
kernel6.17 and null-network support. It does not establish nsenter/ip/iptables
availability or permission to enter a network namespace. The dedicated CI must
install trusted host packages iproute2, iptables and util-linux, then run:

    sudo -n "$(command -v python)" tools/p07_timeout_topology_probe.py --output worker

The probe returns success only for synthetic_timeout_topology_qualified. Its
calibrated and verified_success fields remain false. This profile is not yet
integrated into any Requests oracle adapter. Fresh protected base/gold/wrong
triples on the admitted legacy runtime, CN-only certificate qualification and
negative-control review remain separate gates.
