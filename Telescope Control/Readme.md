This folder holds the code to control the telescope. 

  - Nexstar_methods.py outlines the serial commands to control the telescope and should be imported to use those commands, as well as some constants used throughout the other files
  
  - OGS_control_methods.py is the file defining how we actually control the telescope, as well as defining the generation of a schedule of slew rates from the calculated path. It contains a schedule_gen class and a OGS controll class. The schedule_gen class takes in a position as a function fo time and creates a schedule of commands, OGS_control has commands to actually slew the telescope and follow the schedule created by schedule_gen
  - Main_Controller.py is the file that is actually run to control the telescope during a pass.
  
  - Slew rate calculations contains the calculations to obtain the slew rates necessary to track the satellite, goal is to create a function
  that has as its only input being time and can be fed into schedule_gen.

  - From March 2nd 2025, the tracking methods are tested by the RMS of the measured path vs the intended path. The tracking_methods_recods       
  jupyter notebook keeps track of how well tracking methods are matching intended paths.
