server: server.py
	echo '#!/bin/bash\nexec python3 server.py "$$@"' > rserver
	chmod +x rserver

clean:
	rm -f rserver