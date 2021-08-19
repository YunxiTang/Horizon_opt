#include "ilqr_impl.h"


bool IterativeLQR::forward_pass(double alpha)
{
    TIC(forward_pass);

    // reset values
    _fp_res->accepted = false;
    _fp_res->alpha = alpha;
    _fp_res->step_length = 0.0;

    // initialize forward pass with initial state
    _fp_res->xtrj.col(0) = _xtrj.col(0);

    // do forward pass
    for(int i = 0; i < _N; i++)
    {
        forward_pass_iter(i, alpha);
    }

    // set cost value and constraint violation after the forward pass
    _fp_res->cost = compute_cost(_fp_res->xtrj, _fp_res->utrj);
    _fp_res->constraint_violation = compute_constr(_fp_res->xtrj, _fp_res->utrj);
    _fp_res->defect_norm = compute_defect(_fp_res->xtrj, _fp_res->utrj);

    return true;
}

void IterativeLQR::forward_pass_iter(int i, double alpha)
{
    TIC(forward_pass_inner)

    // note!
    // this function will update the control at t = i, and
    // the state at t = i+1

    // some shorthands
    const auto xnext = state(i+1);
    const auto xi = state(i);
    const auto ui = input(i);
    const auto xi_upd = _fp_res->xtrj.col(i);
    auto& tmp = _tmp[i];
    tmp.dx = xi_upd - xi;

    // dynamics
    const auto& dyn = _dyn[i];
    const auto& A = dyn.A();
    const auto& B = dyn.B();
    const auto& d = dyn.d;

    // backward pass solution
    const auto& res = _bp_res[i];
    const auto& L = res.Lu;
    auto l = alpha * res.lu;

    // update control
    auto ui_upd = ui + l + L*tmp.dx;
    _fp_res->utrj.col(i) = ui_upd;

    // update next state
    auto xnext_upd = xnext + (A + B*L)*tmp.dx + B*l + alpha*d;
    _fp_res->xtrj.col(i+1) = xnext_upd;

#if false
    // compute largest multiplier..
    // ..for dynamics (lam_x = S*dx + s)
    tmp.lam_x = _value[i].S*tmp.dx + _value[i].s;
    _fp_res->lam_x_max = std::max(_fp_res->lam_x_max, tmp.lam_x.cwiseAbs().maxCoeff());

    // ..for constraints (lam_g = TBD)
    if(res.nc > 0)
    {
        tmp.lam_g = res.glam + res.Gu*(l + L*tmp.dx) + res.Gx*tmp.dx;
        _fp_res->lam_g_max = std::max(_fp_res->lam_g_max, tmp.lam_g.cwiseAbs().maxCoeff());
    }
#endif

    // compute step length
    _fp_res->step_length += l.cwiseAbs().sum();

}

double IterativeLQR::compute_merit_slope(double mu_f, double mu_c,
                                         double defect_norm, double constr_viol)
{
    // see Nocedal and Wright, Theorem 18.2, pg. 541
    // available online http://www.apmath.spbu.ru/cnsa/pdf/monograf/Numerical_Optimization2006.pdf

    double der = 0.;

    for(int i = 0; i < _N; i++)
    {
        auto& hu = _tmp[i].hu;
        auto& lu = _bp_res[i].lz;

        der += lu.dot(hu);
    }

    return der - mu_f*defect_norm - mu_c*constr_viol;
}

double IterativeLQR::compute_merit_value(double mu_f,
                                         double mu_c,
                                         double cost,
                                         double defect_norm,
                                         double constr_viol)
{
    // we define a merit function as follows
    // m(alpha) = J + mu_f * |D| + mu_c * |G|
    // where:
    //   i) J is the cost
    //  ii) D is the vector of defects (or gaps)
    // iii) G is the vector of equality constraints
    //  iv) mu_f (feasibility) is an estimate of the largest lag. mult.
    //      for the dynamics constraint (a.k.a. co-state)
    //   v) mu_c (constraint) is an estimate of the largest lag. mult.
    //      for the equality constraints

    return cost + mu_f*defect_norm + mu_c*constr_viol;
}


std::pair<double, double> IterativeLQR::compute_merit_weights()
{
    // note: we here assume dx = 0, since this function runs before
    // the forward pass

    double lam_x_max = 0.0;
    double lam_g_max = 0.0;

    for(int i = 0; i < _N; i++)
    {
        auto& res = _bp_res[i];
        auto& l = res.lu;

        // compute largest multiplier..
        // ..for dynamics (lam_x = S*dx + s)
        _lam_x.col(i) = _value[i].s;
        lam_x_max = std::max(lam_x_max, _lam_x.col(i).cwiseAbs().maxCoeff());

        // ..for constraints (lam_g = TBD)
        if(res.nc > 0)
        {
            _lam_g[i] = res.glam + res.Gu*l;
            lam_g_max = std::max(lam_g_max, _lam_g[i].cwiseAbs().maxCoeff());
        }
    }


    const double merit_safety_factor = 2.0;
    double mu_f = lam_x_max * merit_safety_factor;
    double mu_c = lam_g_max * merit_safety_factor;

    return {mu_f, mu_c};

}

double IterativeLQR::compute_cost(const Eigen::MatrixXd& xtrj, const Eigen::MatrixXd& utrj)
{
    double cost = 0.0;

    // intermediate cost
    for(int i = 0; i < _N; i++)
    {
        cost += _cost[i].evaluate(xtrj.col(i), utrj.col(i));
    }

    // add final cost
    // note: u not used
    // todo: enforce this!
    cost += _cost[_N].evaluate(xtrj.col(_N), utrj.col(_N-1));

    return cost / _N;
}

double IterativeLQR::compute_constr(const Eigen::MatrixXd& xtrj, const Eigen::MatrixXd& utrj)
{
    double constr = 0.0;

    // intermediate constraint violation
    for(int i = 0; i < _N; i++)
    {
        if(!_constraint[i].is_valid())
        {
            continue;
        }

        _constraint[i].evaluate(xtrj.col(i), utrj.col(i));
        constr += _constraint[i].h().cwiseAbs().sum();
    }

    // add final constraint violation
    if(_constraint[_N].is_valid())
    {
        // note: u not used
        // todo: enforce this!
        _constraint[_N].evaluate(xtrj.col(_N), utrj.col(_N-1));
        constr += _constraint[_N].h().cwiseAbs().sum();
    }

    return constr / _N;
}

double IterativeLQR::compute_defect(const Eigen::MatrixXd& xtrj, const Eigen::MatrixXd& utrj)
{
    double defect = 0.0;

    // compute defects on given trajectory
    for(int i = 0; i < _N; i++)
    {
        _dyn[i].computeDefect(xtrj.col(i),
                              utrj.col(i),
                              xtrj.col(i+1),
                              _tmp[i].defect);

        defect += _tmp[i].defect.cwiseAbs().sum();
    }

    return defect / _N;
}

void IterativeLQR::line_search(int iter)
{
    TIC(line_search);

    const double step_reduction_factor = 0.5;
    const double alpha_min = 0.001;
    double alpha = 1.0;
    const double eta = 1e-4;

    // compute merit function weights
    auto [mu_f, mu_c] = compute_merit_weights();

    _fp_res->mu_f = mu_f;
    _fp_res->mu_c = mu_c;

    // compute merit function initial value
    double merit = compute_merit_value(mu_f, mu_c,
            _fp_res->cost,
            _fp_res->defect_norm,
            _fp_res->constraint_violation);

    if(iter == 0)
    {
        _fp_res->merit = merit;
        report_result(*_fp_res);
    }

    // compute merit function directional derivative
    double merit_der = compute_merit_slope(mu_f, mu_c,
            _fp_res->defect_norm,
            _fp_res->constraint_violation);

    _fp_res->merit_der = merit_der;

    // run line search
    while(alpha >= alpha_min)
    {
        // run forward pass
        forward_pass(alpha);

        // compute merit
        _fp_res->merit = compute_merit_value(mu_f, mu_c,
                                             _fp_res->cost,
                                             _fp_res->defect_norm,
                                             _fp_res->constraint_violation);

        // evaluate Armijo's condition
        _fp_res->accepted = _fp_res->merit <= merit + eta*alpha*merit_der;

        // invoke user defined callback
        report_result(*_fp_res);

        if(_fp_res->accepted)
        {
            break;
        }

        // reduce step size and try again
        alpha *= step_reduction_factor;
    }

    if(!_fp_res->accepted)
    {
        _fp_res->accepted = true;
        report_result(*_fp_res);
    }

    _xtrj = _fp_res->xtrj;
    _utrj = _fp_res->utrj;
}

bool IterativeLQR::should_stop()
{
    // first, evaluate feasibility
    if(_fp_res->constraint_violation > 1e-6)
    {
        return false;
    }

    if(_fp_res->defect_norm > 1e-6)
    {
        return false;
    }

    // here we're feasible

    // exit if merit function directional derivative (normalized)
    // is too close to zero
    if(_fp_res->merit_der/_fp_res->merit > -1e-9)
    {
        return true;
    }

    // exit if step size (normalized) is too short
    if(_fp_res->step_length/_utrj.norm() < 1e-9)
    {
        return true;
    }

    return false;
}