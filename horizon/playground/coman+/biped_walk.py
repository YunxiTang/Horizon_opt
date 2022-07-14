import os
import numpy as np
from horizon.rhc.taskInterface import TaskInterface
from horizon.transcriptions.transcriptor import Transcriptor
# set up problem
ns = 50
tf = 10.0  # 10s
dt = tf / ns

# set up solver
solver_type = 'ipopt'
transcription_method = 'multiple_shooting'  # can choose between 'multiple_shooting' and 'direct_collocation'
transcription_opts = dict(integrator='RK4')  # integrator used by the multiple_shooting

# set up model
path_to_examples = os.path.abspath(__file__ + "/../../../examples")
os.environ['ROS_PACKAGE_PATH'] += ':' + path_to_examples

urdffile = os.path.join(path_to_examples, 'urdf', 'cogimon.urdf')
urdf = open(urdffile, 'r').read()

contacts = ['l_sole', 'r_sole']

base_init = np.array([0., 0., 0.96, 0., 0.0, 0.0, 1.])

q_init = {"LHipLat":       -0.0,
          "LHipSag":       -0.363826,
          "LHipYaw":       0.0,
          "LKneePitch":    0.731245,
          "LAnklePitch":   -0.307420,
          "LAnkleRoll":    0.0,
          "RHipLat":       0.0,
          "RHipSag":       -0.363826,
          "RHipYaw":       0.0,
          "RKneePitch":    0.731245,
          "RAnklePitch":   -0.307420,
          "RAnkleRoll":    -0.0,
          "WaistLat":      0.0,
          "WaistYaw":      0.0,
          "LShSag":        1.1717860 ,    #0.959931,   #   1.1717860
          "LShLat":         -0.059091562,    #0.007266,   #   -0.059091562
          "LShYaw":         -5.18150657e-02,    #-0.0,       #   -5.18150657e-02
          "LElbj":         -1.85118,    #-1.919862,  #  -1.85118
          "LForearmPlate":  0.0,
          "LWrj1":          -0.523599,
          "LWrj2":          -0.0,
          "RShSag":          1.17128697,  #0.959931,    #   1.17128697
          "RShLat":         6.01664139e-02,  #-0.007266,   #   6.01664139e-02
          "RShYaw":         0.052782481,  #-0.0,        #   0.052782481
          "RElbj":          -1.8513760,  #-1.919862,   #   -1.8513760
          "RForearmPlate":  0.0,
          "RWrj1":          -0.523599,
          "RWrj2":          -0.0}

problem_opts = {'ns': ns, 'tf': tf, 'dt': dt}

model_description = 'whole_body'
ti = TaskInterface(urdf, q_init, base_init, problem_opts, model_description, contacts=contacts, enable_torques=True)

ti.loadPlugins(['horizon.rhc.plugins.contactTaskMirror'])
ti.setTaskFromYaml('config_walk.yaml')

# for task in ti.task_list:
#     print(task)

f0 = np.array([0, 0, 55, 0, 0, 0])
init_force = ti.getTask('joint_regularization')
init_force.setRef(1, f0)
init_force.setRef(2, f0)

if solver_type != 'ilqr':
    th = Transcriptor.make_method(transcription_method, ti.prb, opts=transcription_opts)
# l_contact = ti.getTask('foot_contact_l_foot')
# l_contact.setNodes(list(range(10, 80)))
# ===============================================================
# ===============================================================
# ===============================================================
q = ti.prb.getVariables('q')
v = ti.prb.getVariables('v')

q.setBounds(ti.q0, ti.q0, nodes=0)
v.setBounds(ti.v0, ti.v0, nodes=0)

q.setInitialGuess(ti.q0)

forces = [ti.prb.getVariables('f_' + c) for c in contacts]

for f in forces:
    f.setInitialGuess(f0)

# ================================================================
# ================================================================
# ===================== stuff to wrap ============================
# ================================================================
# ================================================================
import subprocess, rospy
from horizon.ros import replay_trajectory

# todo
# ptgt.assign(ptgt_final, nodes=ns)

solver_bs, solver_rti = ti.getSolver()
solver_bs.solve()
solution = solver_bs.getSolutionDict()

os.environ['ROS_PACKAGE_PATH'] += ':' + path_to_examples
subprocess.Popen(["roslaunch", path_to_examples + "/replay/launch/launcher.launch", 'robot:=cogimon'])
rospy.loginfo("'cogimon' visualization started.")

# single replay
q_sol = solution['q']
print(q_sol[:, ns-1])
frame_force_mapping = {contacts[i]: solution[forces[i].getName()] for i in range(2)}
repl = replay_trajectory.replay_trajectory(dt, ti.kd.joint_names()[2:], q_sol, frame_force_mapping, ti.kd_frame, ti.kd)
repl.sleep(1.)
repl.replay(is_floating_base=True)
# =========================================================================
from horizon.utils import plotter
import matplotlib.pyplot as plt
from matplotlib import gridspec
import casadi as cs

plot_sol = True
if plot_sol:

    hplt = plotter.PlotterHorizon(ti.prb, solution)
    hplt.plotVariables([elem.getName() for elem in forces], show_bounds=False, gather=2, legend=False)
    hplt.plotFunction('dynamics', show_bounds=True, legend=True, dim=range(6))

    # some custom plots to visualize the robot motion
    fig = plt.figure()
    fig.suptitle('Contacts')
    gs = gridspec.GridSpec(1, 2)
    i = 0
    for contact in contacts:
        ax = fig.add_subplot(gs[i])
        ax.set_title('{}'.format(contact))
        i += 1
        FK = cs.Function.deserialize(ti.kd.fk(contact))
        pos = FK(q=solution['q'])['ee_pos']
        for dim in range(3):
            ax.plot(np.array([range(pos.shape[1])]), np.array(pos[dim, :]), marker="x", markersize=3, linestyle='dotted')

    plt.figure()
    for contact in contacts:
        FK = cs.Function.deserialize(ti.kd.fk(contact))
        pos = FK(q=solution['q'])['ee_pos']

        plt.title(f'plane_xy')
        plt.scatter(np.array(pos[0, :]), np.array(pos[1, :]), linewidth=0.1)

    plt.figure()
    for contact in contacts:
        FK = cs.Function.deserialize(ti.kd.fk(contact))
        pos = FK(q=solution['q'])['ee_pos']

        plt.title(f'plane_xz')
        plt.scatter(np.array(pos[0, :]), np.array(pos[2, :]), linewidth=0.1)

    plt.show()


# repl = replay_trajectory.replay_trajectory(dt, ti.kd.joint_names()[2:], np.array([]), {k: None for k in contacts},
#                                            ti.kd_frame, ti.kd)
# iteration = 0
#
# solver_rti.solution_dict['x_opt'] = solver_bs.getSolutionState()
# solver_rti.solution_dict['u_opt'] = solver_bs.getSolutionInput()
#
# flag_action = 1
# forces = [ti.prb.getVariables('f_' + c) for c in contacts]
# nc = 4
# while True:
#     iteration = iteration + 1
#     print(iteration)
#     #
#     am.execute(solver_rti)
#     solver_rti.solve()
#     solution = solver_rti.getSolutionDict()
#
#     repl.frame_force_mapping = {contacts[i]: solution[forces[i].getName()][:, 0:1] for i in range(nc)}
#     repl.publish_joints(solution['q'][:, 0])
#     repl.publishContactForces(rospy.Time.now(), solution['q'][:, 0], 0)