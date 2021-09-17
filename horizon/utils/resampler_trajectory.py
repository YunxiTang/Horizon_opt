from casadi_kin_dyn import pycasadi_kin_dyn as cas_kin_dyn
from horizon.utils import kin_dyn
from horizon.transcriptions import integrators
import numpy as np
import casadi as cs

def resample_torques(p, v, a, node_time, dt, dae, frame_force_mapping, kindyn, force_reference_frame = cas_kin_dyn.CasadiKinDyn.LOCAL):
    """
        Resample solution to a different number of nodes, RK4 integrator is used for the resampling
        Args:
            p: position
            v: velocity
            a: acceleration
            node_time: previous node time
            dt: resampled period
            dae: a dictionary containing
                    'x': state
                    'p': control
                    'ode': a function of the state and control returning the derivative of the state
                    'quad': quadrature term
            frame_force_mapping: dictionary containing a map between frames and force variables e.g. {'lsole': F1}
            kindyn: object of type casadi_kin_dyn
            force_reference_frame: this is the frame which is used to compute the Jacobian during the ID computation:
                    LOCAL (default)
                    WORLD
                    LOCAL_WORLD_ALIGNED

        Returns:
            p_res: resampled p
            v_res: resampled v
            a_res: resampled a
            frame_res_force_mapping: resampled frame_force_mapping
            tau_res: resampled tau
        """

    p_res, v_res, a_res = second_order_resample_integrator(p, v, a, node_time, dt, dae)
    ni = a_res.shape[1]
    frame_res_force_mapping = dict()
    for key in frame_force_mapping:
        frame_res_force_mapping[key] = np.zeros([frame_force_mapping[key].shape[0], ni])

    number_of_nodes = p.shape[1]
    node_time_array = np.zeros([number_of_nodes])
    if hasattr(node_time, "__iter__"):
        for i in range(1, number_of_nodes):
            node_time_array[i] = node_time_array[i-1] + node_time[i - 1]
    else:
        for i in range(1, number_of_nodes):
            node_time_array[i] = node_time_array[i - 1] + node_time
    t = 0.
    node = 0
    i = 0
    while i < a_res.shape[1]-1:
        for key in frame_force_mapping:
            frame_res_force_mapping[key][:, i] = frame_force_mapping[key][:, node]
        t += dt
        i += 1
        if t > node_time_array[node+1]:
            node += 1

    tau_res = np.zeros(a_res.shape)

    ID = kin_dyn.InverseDynamics(kindyn, frame_force_mapping.keys(), force_reference_frame)
    for i in range(ni):
        frame_force_map_i = dict()
        for frame, wrench in frame_res_force_mapping.items():
            frame_force_map_i[frame] = wrench[:, i]
        tau_i = ID.call(p_res[:, i], v_res[:, i], a_res[:, i], frame_force_map_i)
        tau_res[:, i] = tau_i.toarray().flatten()

    return p_res, v_res, a_res, frame_res_force_mapping, tau_res

def second_order_resample_integrator(p, v, a, node_time, dt, dae):
    """
    Resample a solution with the given dt
    Args:
        p: position
        v: velocity
        a: acceleration
        node_time: previous node time
        dt: resampling time
        dae: dynamic model
    Return:
        p_res: resampled position
        v_res: resampled velocity
        a_res: resampled acceleration
    """
    number_of_nodes = p.shape[1]
    node_time_array = np.zeros([number_of_nodes])
    if hasattr(node_time, "__iter__"):
        for i in range(1, number_of_nodes):
            node_time_array[i] = node_time_array[i-1] + node_time[i - 1]
    else:
        for i in range(1, number_of_nodes):
            node_time_array[i] = node_time_array[i-1] + node_time

    n_res = int(round(node_time_array[-1]/dt))

    opts = {'tf': dt}
    F_integrator = integrators.RK4(dae, opts, cs.SX)

    x_res0 = np.hstack((p[:, 0], v[:, 0]))

    x_res = np.zeros([p.shape[0] + v.shape[0], n_res+1])
    p_res = np.zeros([p.shape[0], n_res+1])
    v_res = np.zeros([v.shape[0], n_res+1])
    a_res = np.zeros([a.shape[0], n_res])

    x_res[:, 0] = x_res0
    p_res[:, 0] = x_res0[0:p.shape[0]]
    v_res[:, 0] = x_res0[p.shape[0]:]
    a_res[:, 0] = a[:, 0]

    t = 0.
    i = 0
    node = 0
    while i < a_res.shape[1]-1:
        x_resi = F_integrator(x0=x_res[:, i], p=a[:, node])['xf'].toarray().flatten()

        t += dt
        i += 1

        #print(f"{t} <= {tf-dt} @ node time {(node+1)*node_time} i: {i}")

        x_res[:, i] = x_resi
        p_res[:, i] = x_resi[0:p.shape[0]]
        v_res[:, i] = x_resi[p.shape[0]:]
        a_res[:, i] = a[:, node]

        if t > node_time_array[node+1]:
            new_dt = t - node_time_array[node+1]
            node += 1
            if new_dt >= 1e-6:
                opts = {'tf': new_dt}
                new_F_integrator = integrators.RK4(dae, opts, cs.SX)
                x_resi = new_F_integrator(x0=np.hstack((p[:,node], v[:,node])), p=a[:, node])['xf'].toarray().flatten()
                x_res[:, i] = x_resi
                p_res[:, i] = x_resi[0:p.shape[0]]
                v_res[:, i] = x_resi[p.shape[0]:]
                a_res[:, i] = a[:, node]


    x_resf = np.hstack((p[:, -1], v[:, -1]))
    x_res[:, -1] = x_resf
    p_res[:, -1] = x_resf[0:p.shape[0]]
    v_res[:, -1] = x_resf[p.shape[0]:]

    return p_res, v_res, a_res


def resampler(state_vec, input_vec, nodes_dt, desired_dt):

    # convert to np if not np already
    states = np.array(state_vec)
    inputs = np.array(input_vec)

    state_dim = states.shape[0]
    input_dim = inputs.shape[0]
    n_nodes = states.shape[1]



    # construct array of times for each node (nodes could be of different time lenght)
    node_time_array = np.zeros([n_nodes])
    if hasattr(nodes_dt, "__iter__"):
        # if a list of times is passed, construct from this list (used when variable time node)
        for i in range(1, n_nodes):
            node_time_array[i] = node_time_array[i - 1] + nodes_dt[i - 1]
    else:
        # if a number is passed, construct from this number (used when constant time node)
        for i in range(1, n_nodes):
            node_time_array[i] = node_time_array[i - 1] + nodes_dt


    # number of nodes in resampled trajectory
    n_nodes_res = int(round(node_time_array[-1] / desired_dt)) + 1

    var_dim = 2

    state_abst = cs.SX.sym('state_abst', state_dim)
    input_abst = cs.SX.sym('input_abst', input_dim)
    state_dot = cs.vertcat(state_abst[var_dim:], input_abst)

    L = 1
    dae = {'x': state_abst, 'p': input_abst, 'ode': state_dot, 'quad': L}
    opts = {'tf': desired_dt}

    F_integrator = integrators.RK4(dae, opts, cs.SX)

    # initialize resapmpled trajectories
    state_res = np.zeros([state_dim, n_nodes_res]) # state: number of resampled nodes
    input_res = np.zeros([input_dim, n_nodes_res - 1]) # input: number of resampled nodes - 1

    state_res[:, 0] = states[:, 0]
    input_res[:, 0] = inputs[:, 0]

    t = 0.
    i = 0
    node = 0
    while i < input_res.shape[1] - 1:
        state_res_i = F_integrator(x0=state_res[:, i], p=inputs[:, node])['xf'].toarray().flatten()

        t += desired_dt
        i += 1

        state_res[:, i] = state_res_i
        input_res[:, i] = inputs[:, node]

        # this is required if the current t goes beyond the current node time.
        # I get new_dt, the exceeding time (t-node_time_array[node+1]
        if t > node_time_array[node + 1]:
            new_dt = t - node_time_array[node + 1]
            node += 1
            if new_dt >= 1e-6:
                # I set the new_dt as the integrator time
                opts = {'tf': new_dt}
                new_F_integrator = integrators.RK4(dae, opts, cs.SX)
                # integrate from the node i just exceed with the relative input for the exceeding time
                state_res_i = new_F_integrator(x0=states[:, node], p=inputs[:, node])[
                    'xf'].toarray().flatten()
                state_res[:, i] = state_res_i
                input_res[:, i] = inputs[:, node]

    # the last node of the resampled trajectory has the same value as the original trajectory
    state_res[:, -1] = states[:, -1]

    return state_res

if __name__ == '__main__':

    np.set_printoptions(precision=3, suppress=True)

    tf = 1.0
    nodes_dt = 0.5
    n_nodes = int(tf / nodes_dt) + 1
    new_nodes_dt = 0.005
    print(f"n_time: {nodes_dt} (n. nodes {n_nodes})---> {new_nodes_dt} (n. nodes {int(tf / new_nodes_dt) + 1})")

    p = np.ones([2, n_nodes])
    v = np.ones([2, n_nodes])

    p[0, 0] = 0.5 * p[0, 0]
    p[1, 0] = -0.5 * p[1, 0]
    v[0, 0] = 0.5 * v[0, 0]
    v[1, 0] = 0   * v[1, 0]

    p[0, -1] = 1 * p[0, -1]
    p[1, -1] = -1 * p[1, -1]
    v[0, -1] = 0 * v[0, -1]
    v[1, -1] = 0 * v[1, -1]



    print(p)
    print(v)

    inputs = 0.1 * np.ones([2, n_nodes-1]) # input

    print(inputs.shape)
    states = cs.vertcat(p, v)


    # nodes_dt = [0.01, 0.02, 0.01, 0.01, 0.02, 0.03, 0.02, 0.01, 0.01, 0.01]
    # nodes_dt = [0.01, 0.02, 0.01, 0.01, 0.02, 0.03, 0.02, 0.01, 0.01, 0.01]
    state_res = resampler(states, inputs, nodes_dt, new_nodes_dt)

    print('state_res.shape', state_res.shape)
    print(state_res[0,:])
    # ===============================================================
    print(' ==================== other method ========================')
    state = cs.SX.sym('state', 4)
    input = cs.SX.sym('input', 2)
    state_dot = cs.vertcat(state[2:], input)

    a = inputs
    L = 1
    dae = {'x': state, 'p': input, 'ode': state_dot, 'quad': L}
    p_res, v_res, a_res = second_order_resample_integrator(p, v, a, nodes_dt, new_nodes_dt, dae)

    print('p_res.shape', p_res.shape)
    print('a_res.shape', a_res.shape)

    print(p_res[0, :])

