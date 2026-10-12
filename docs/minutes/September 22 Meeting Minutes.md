# COSC 499 Meeting Minutes

Date	September 22nd, 2026
Time	11:00am-12:00pm

## 1. Attendees
Kaedyn    Fabiha    Mia

## 2. Agenda
1. Project architecture and design discussion
2. Key points to discuss with client

## 3. Meeting Minutes
### 1.
    • Mia lead a discussion on the project design. 
        ◦ React was decided on for the front-end of the application for now.  Fabiha and Kaedyn are both familiar with React.
        ◦ Python is currently the language of choice for the backend for easy API connectivity.
        ◦ Uvicorn is currently the server service of choice for python, although discussion with the client may change this choice. 
        ◦ PostgreSql was decided as the database of choice, discussion with the client may change this choice.
        ◦ The application will be deployed with Docker
        ◦ HTTPS is required for GPS and camera APIs in browsers
    • Discussion was held on expanding the game design with AI assistance. Base requirements should be focused on first, and if there is additional time, an AI assisted mode can allow generating more audio for more rooms and buildings.
    • Discussion was held on the project proposal due Sunday. The architecture decisions are confident thus far, but the proposal requires listing multiple options, the pros and cons of each, and why our team has decided on what they did. Kaedyn said he would work on preparing the proposal document for easier completion during and after Thursday’s meeting.

### 2.
    • The GPS tracking and computer vision, as it is unsure how accurate they must be.
    • Game design. Client expectations of difficulty, badge/reward system, room randomization
    • Security concerns. We already need HTTPS but are there additional requirements?
    • Sign-in process. Do we need to integrate SSO authentication?
    • What student data to track.

### Action Points
    • Meeting scheduled after class/client meeting on Thursday September 24th to cover architecture design and data flow documents.
    • Work on project proposal document. Should be finished by Sunday morning.

## Next Meeting
    • Discuss and solidify plans for project proposal