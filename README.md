# Motion Freight Router & API Demos
This repo contains the main Motion Routing API as well as two demos. To run all of these, run the startup.sh script (if you are on Windows, run startup.sh in WSL). Building the graph will take quite a while on first startup (~20 minutes), the Routing API will not function correctly until the graph is built.  
```./startup.sh```  
  
Running docker compose down DOES NOT! stop everything. To stop everything, use:  
```./stop.sh```  
this will not, however, clean up storage; the easiest method to free up space is to fully trash/remove/rm/discard the motion repository and remove the docker containers.  
  
API URL: [http://localhost:8000](http://localhost:8000)  
Visual:  [http://localhost:8001](http://localhost:8001)  
curlgen: [http://localhost:8002](http://localhost:8002)  
Docs:    [https://motion-aaad6afa.mintlify.app](https://motion-aaad6afa.mintlify.app)  
  
Will be disabled 03/01/2026 3pm CST  
Public Temporary API URL: https://motion.rthak.com/  
Public Temporary curlgen: [https://mgen.rthak.com/](https://mgen.rthak.com/)  
  
Transparency Notice: This repository was created for HackIllinois 2026 and is not a polished or well-secured and well-tested project. AI assisted code generation was used to create some portion of this repository.
