import socket
import selectors
import struct
import sys
import time
import random
import heapq

MAGIC = 0x0417
selector = selectors.DefaultSelector()
clients = {}
used_indices = set()
free_indices = []


class ClientState:
    HANDSHAKE = "HANDSHAKE"
    CONNECTED = "CONNECTED"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    INVALID = "INVALID"

class Client:
    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.buffer = b''
        self.outgoing = []
        self.room = None
        self.nick = None
        self.state = ClientState.HANDSHAKE
class Room:
    def __init__(self, name, password):
        self.name = name
        self.password = password
        self.clients = set()

    def add_client(self, client):
        self.clients.add(client)
    
    def remove_client(self, client):
        self.clients.discard(client)

    def is_empty(self):
        return len(self.clients) == 0
    
rooms: dict[str, Room] = {}

def start_server(host, port):
    random.seed(time.time())
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, port)) # check if bindinng actually works
        sock.listen()
        sock.setblocking(False)
        selector.register(sock, selectors.EVENT_READ, data=None)
        return sock
    except OSError as e:
        print(f"Socket failed: {e}", file=sys.stderr)
        sys.exit(1)

def parse_args():
    if len(sys.argv) != 3 or sys.argv[1] != '-p':
        print("Usage: python server.py -p <port>")
        sys.exit(1)
    try:
        port = int(sys.argv[2])
        if not (0 < port < 65536):
            raise ValueError
        return port
    except ValueError:
        print("Error: Port must be a valid integer between 1 and 65535.")
        sys.exit(1)
    
def flush_outgoing(client):
    while client.outgoing:
        try:
            data = client.outgoing[0]
            print(f"Sending to {client.addr}: {client.outgoing[0].hex()}")
            sent = client.sock.send(data)

            if sent < len(data):
                client.outgoing[0] = data[sent:]
                break
            else:
                client.outgoing.pop(0)

        except BlockingIOError:
            break
        except Exception as e:
            print(f"Error sending to {client.addr}: {e}")
            cleanup_client(client)
            break

def accept_client(server_socket):
    try:
        client_sock, addr = server_socket.accept()
        client_sock.setblocking(False)
        client = Client(client_sock, addr)
        clients[client_sock] = client
        selector.register(client_sock, selectors.EVENT_READ, data=client)
        print(f"Accepted connection from {addr}")
    except Exception as e:
        print(f"Failed to accept client: {e}")

def cleanup_client(client):
    try:
        selector.unregister(client.sock)
    except Exception:
        pass
    
    try:
        client.sock.close()
    except Exception:
        pass

    if client.room and client.room in rooms:
        room = rooms[client.room]
        room.remove_client(client)
        if room.is_empty():
            del rooms[client.room]

    if client.nick and client.nick.startswith("rand"):
        try:
            index = int(client.nick[4:])
            if index in used_indices:
                used_indices.remove(index)
                heapq.heappush(free_indices, index)
        except ValueError:
            pass
    clients.pop(client.sock, None)

def build_message(opcode: int, payload: bytes) -> bytes:
    length_prefix = struct.pack("!I", len(payload))  # just the payload
    header = struct.pack("!H", MAGIC) + bytes([opcode])
    return length_prefix + header + payload

def handle_join(client, payload: bytes):
    # be CAREFUL for when you are closing your shit
    if len(payload) < 2:
        # client.state = 'CLOSING'
        return

    room_len = payload[0]
    if len(payload) < 1 + room_len + 1:
        # client.state = 'CLOSING'
        return

    room_name = payload[1:1 + room_len].decode()
    pw_len = payload[1 + room_len]
    password = payload[2 + room_len : 2 + room_len + pw_len].decode()

    if '\x00' in room_name or '\x00' in password:
        client.state = 'CLOSING'
        return
    
    if client.room == room_name:
        if client.state != ClientState.CLOSING:
            msg = b'\x01' + b"You've already apparated into this room. No need for a Time-Turner."
            client.outgoing.append(build_message(0x9a, msg))
        return
    room = rooms.get(room_name)
    if room is None:
        room = Room(room_name, password)
        rooms[room_name] = room
    elif room.password != password:
        if client.state != ClientState.CLOSING:
            err_msg = b'\x01' + b"Incorrect password. Maybe try 'Alohomora'?"
            client.outgoing.append(build_message(0x9a, err_msg))
        return

    if client.room and client.room in rooms:
        old_room = rooms[client.room]
        old_room.remove_client(client)
        if old_room.is_empty():
            del rooms[client.room]

    client.room = room_name
    room.add_client(client)

    client.outgoing.append(build_message(0x9a, b'\x00'))
    print("JOIN RESPONSE BEING SENT:", build_message(0x9a, b'\x00').hex())

def handle_leave(client):
    # 
    if client.room is not None and client.room in rooms:
        room = rooms[client.room]
        print(f"{client.nick} is leaving room {room.name}\n")
        room.remove_client(client)
        if room.is_empty():
            del rooms[client.room]
        client.room = None
        if client.state != ClientState.CLOSING:
            client.outgoing.append(build_message(0x9a, b'\x00'))  # success response - OK BE CAREFUL HERE BECAUSE IM NOT SURE IF YOU NEED THE 01 at the front - NVM I THINK YOURE GOOD
    else:
        print(f"{client.nick} is not in a room, closing connection")
        client.state = ClientState.CLOSING

def handle_list_users(client):
    payload = b'\x00'
    for other in clients.values():
        if other.nick and (client.room is None or other.room == client.room):
            name_bytes = other.nick.encode()
            payload += bytes([len(name_bytes)]) + name_bytes
    if client.state != ClientState.CLOSING:
        client.outgoing.append(build_message(0x9a, payload))
        print("LIST USERS RESPONSE BEING SENT:", build_message(0x9a, payload).hex())

def handle_list_rooms(client):
    payload = b'\x00'
    for room in rooms.values():
        room_bytes = room.name.encode()
        payload += bytes([len(room_bytes)]) + room_bytes
    if client.state != ClientState.CLOSING:
        client.outgoing.append(build_message(0x9a, payload))
        print("LIST ROOMS RESPONSE BEING SENT:", build_message(0x9a, payload).hex())


def handle_message(client, payload: bytes):
    target_len = payload[0]
   
    target_nick = payload[1:1 + target_len].decode()
    msg_len = int.from_bytes(payload[1 + target_len:1 + target_len + 2], 'big')
    print(f'HIIIIII this is the message length: {msg_len}\n')
    # ok this is a message you left out, should be good now for that error
    if msg_len >= 65536:
        err_msg = b'\x01' + b"Length limit exceeded."
        print("MSG ERROR RESPONSE BEING SENT TOO LONG:", build_message(0x9a, err_msg).hex())
        client.outgoing.append(build_message(0x9a, err_msg))
        cleanup_client(client)
        # client.state = ClientState.CLOSING
        return
    # this will make you disconnect normally - command too long ^^^^

    # making sure the entire message is here *_*
    # if len(payload) < 1 + target_len + 2 + msg_len:
    #     print("message length is wrong you shitterton!\n")
    #     # client.state = ClientState.CLOSING
    #     cleanup_client(client)
    #     return
    
    message = payload[1 + target_len + 2 : 1 + target_len + 2 + msg_len].decode()
    print(f"MSG from {client.nick} to {target_nick}: {message}")

    recipient = None
    for other in clients.values():
        if other.nick == target_nick:
            recipient = other
            break
    if not recipient:
        if client.state != ClientState.CLOSING:
            err_msg = b'\x01' + b"That wizard isn't here. Maybe try the Room of Requirement?"
            print("MSG ERROR RESPONSE BEING SENT:", build_message(0x9a, err_msg).hex())
            client.outgoing.append(build_message(0x9a, err_msg))
            return
    
    sender_nick = client.nick.encode()
    msg_bytes = message.encode()
    payload = (
        bytes([len(sender_nick)]) + sender_nick +
        struct.pack("!H", len(msg_bytes)) + msg_bytes
    )

    recipient.outgoing.append(build_message(0x12, payload))
    client.outgoing.append(build_message(0x9a, b'\x00'))

def handle_nick(client, payload: bytes):
    # error handling done here
    if len(payload) < 1:
        print("Invalid command.\n")
        # client.state = ClientState.CLOSING
        return
    name_len = payload[0]
    if name_len > 255:
        print("Nick is longer than 255 characters.\n")
        # client.state = ClientState.CLOSING
        return
    new_nick = payload[1:1 + name_len].decode()

    for other in clients.values():
        if other != client and other.nick == new_nick:
            if client.state != ClientState.CLOSING:
                err_msg = b'\x01' + b"That name's already on the Marauder's Map. Choose another.\n"
                client.outgoing.append(build_message(0x9a, err_msg))
                return

    if client.nick and client.nick.startswith("rand") and not new_nick.startswith("rand"):
        try:
            index = int(client.nick[4:])
            if index in used_indices:
                used_indices.remove(index)
                heapq.heappush(free_indices, index)
        except ValueError:
            pass

    client.nick = new_nick
    if client.state != ClientState.CLOSING:
        client.outgoing.append(build_message(0x9a, b'\x00'))

def handle_no_slash(client):
    if not client.room or client.room not in rooms:
        if client.state != ClientState.CLOSING:
            err_msg = b'\x01' + b"You're talking to the walls. No one is here to listen."
            client.outgoing.append(build_message(0x9a, err_msg))
        return

    if client.state != ClientState.CLOSING:
        client.outgoing.append(build_message(0x9a, b'\x00'))

def handle_sorting_hat(client):
    base = "rand"
    if free_indices:
        i = heapq.heappop(free_indices)
    else:
        i = 0
        while i in used_indices:
            i += 1
    used_indices.add(i)
    client.nick = f"{base}{i}"
    client.state = 'CONNECTED'

    response = b'\x00' + client.nick.encode() 
    if client.state != ClientState.CLOSING:
        client.outgoing.append(build_message(0x9a, response))
    
def read_from_client(client):
    try:
        data = client.sock.recv(4096)
        print(data)
        if not data:
            return False  # client closed connection
        
        client.buffer += data

        while len(client.buffer) >= 7:
            magic = int.from_bytes(client.buffer[4:6], 'big')
            if magic != MAGIC:
                return False

            length = int.from_bytes(client.buffer[0:4], 'big')
            if len(client.buffer) < 7 + length:
                break  # wait for full packet

            opcode = client.buffer[6]
            payload = client.buffer[7:7+length]
            client.buffer = client.buffer[7+length:]  # advance buffer

            if opcode == 0x03:
                print("am i here???/")
                print("handling JOIN")
                handle_join(client, payload)
            elif opcode == 0x06:
                handle_leave(client)
            elif opcode == 0x0c:
                handle_list_users(client)
            elif opcode == 0x09:
                handle_list_rooms(client)
            elif opcode == 0x12:
                handle_message(client, payload)
            elif opcode == 0x0f:
                handle_nick(client, payload)
            elif opcode == 0x15:
                handle_no_slash(client)
            elif opcode == 0x9b:
                handle_sorting_hat(client)
            elif opcode == 0x13:
                continue
            else:
                return False
        return True

    except Exception as e:
        print(f"Read error from {client.addr}: {e}")
        cleanup_client(client)
        return False

def server_run(server_socket):
    print("hi")
    try:
        while True:
            events = selector.select(timeout=1)
            for key, mask in events:
                if key.data is None:
                    accept_client(key.fileobj)
                else:
                    client = key.data
                    if mask & selectors.EVENT_READ:
                        if not read_from_client(client):
                            # don't set state just do this
                            cleanup_client(client)
                            continue
                    if mask & selectors.EVENT_WRITE:
                        flush_outgoing(client)
                    
                    if client.outgoing:
                        selector.modify(client.sock, selectors.EVENT_READ | selectors.EVENT_WRITE, client)
                    else:
                        selector.modify(client.sock, selectors.EVENT_READ, client)
            
            for c in list(clients.values()):
                if c.state == ClientState.CLOSING:
                    cleanup_client(c)
    except Exception as e:
        print(f"Server error: {e}")
            
def main():
    port = parse_args() # error check here
    server_socket = start_server('0.0.0.0', port)
    print(f"Server listening on port {port}")

    try:
        server_run(server_socket)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        server_socket.close()

if __name__ == "__main__":
    main()
